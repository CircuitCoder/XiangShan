#!/usr/bin/env python3
"""Audit the lowered Sbuffer interfaces and emit explicit miter wiring.

Run from the repository root:
    python3 formal/sbuffer/make_miter.py
An optional positional argument selects a different artifacts directory.
The comparison policy below is deliberately specific to the audited Sbuffer.
Unknown outputs or changed DontCare assignments require review.
"""

import argparse
import json
from pathlib import Path
import re


HERE = Path(__file__).resolve().parent
TSO = "io_csrCtrl_tso_enable"
STARTUP_RESET_CYCLES = 3


def require(condition, message):
    if not condition:
        raise ValueError(message)


def signature(port):
    return (port["direction"], len(port["bits"]), port.get("signed", 0),
            port.get("offset", 0), port.get("upto", 0))


def declaration(kind, name, port):
    require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name),
            f"unsupported Verilog identifier: {name}")
    require(not port.get("offset", 0) and not port.get("upto", 0),
            f"review nonstandard bit indexing: {name}")
    width = len(port["bits"])
    signed = " signed" if port.get("signed", 0) else ""
    size = f" [{width - 1}:0]" if width > 1 else ""
    return f"{kind}{signed}{size} {name}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="?", type=Path,
                        default=HERE / "artifacts/DefaultConfig/Sbuffer")
    args = parser.parse_args()
    root = args.artifacts
    modules = {
        side: json.loads((root / f"{side}.json").read_text())["modules"][f"Sbuffer_{side}"]
        for side in ("base", "tip")
    }
    ports = {side: module["ports"] for side, module in modules.items()}
    base, tip = ports["base"], ports["tip"]
    require(not base.keys() - tip.keys(), "base has unmatched ports")
    require(tip.keys() - base.keys() == {TSO}, "unexpected added tip ports")
    require(signature(tip[TSO]) == ("input", 1, 0, 0, 0), "TSO must be a one-bit input")
    for name, port in base.items():
        require(signature(port) == signature(tip[name]), f"interface mismatch: {name}")
        require(port["direction"] in ("input", "output"), f"unsupported port: {name}")
    inputs = {n: p for n, p in base.items() if p["direction"] == "input"}
    outputs = {n: p for n, p in base.items() if p["direction"] == "output"}
    lanes = sorted(int(m[1]) for n in inputs
                   if (m := re.fullmatch(r"io_forward_(\d+)_valid", n)))
    require(lanes == list(range(len(lanes))) and lanes, "unexpected forwarding lanes")
    firrtl = {side: (root / side / "Sbuffer.fir").read_text()
              for side in ("base", "tip")}

    def invalid_both(path):
        pattern = r"^\s*invalidate " + re.escape(path) + r"(?:\s|$)"
        return all(re.search(pattern, text, re.M) for text in firrtl.values())

    # Each output receives an explicit observation policy. Never silently skip
    # an unfamiliar output merely because it currently lowers to a constant.
    policies = {}
    for name in outputs:
        if name in ("io_in_0_ready", "io_in_1_ready", "io_dcache_req_valid",
                    "io_sbempty", "io_flush_empty"):
            policies[name] = ("always", "every post-reset cycle")
        elif name.startswith("io_dcache_req_bits_"):
            qualifier = "base_io_dcache_req_valid || tip_io_dcache_req_valid"
            if name == "io_dcache_req_bits_data":
                policies[name] = ("cache_data", "request valid; compare only masked bytes")
            else:
                require(name.rsplit("_", 1)[1] in ("cmd", "vaddr", "addr", "mask", "id"),
                        f"review cache payload: {name}")
                policies[name] = (qualifier, "request valid, including backpressure")
        elif m := re.fullmatch(r"io_forward_(\d+)_(forwardMaskFast|forwardMask|forwardData)_(\d+)", name):
            lane, field, byte = m.groups()
            if field == "forwardMaskFast":
                policies[name] = (f"io_forward_{lane}_valid", "current forwarding query valid")
            elif field == "forwardMask":
                policies[name] = (f"forward_valid_d_{lane}", "previous forwarding query valid")
            else:
                mask = f"io_forward_{lane}_forwardMask_{byte}"
                policies[name] = (f"forward_valid_d_{lane} && (base_{mask} || tip_{mask})",
                                  "previous query valid; byte forwarded by either DUT")
        elif m := re.fullmatch(r"io_forward_(\d+)_(dataInvalid|matchInvalid|addrInvalid)", name):
            lane, field = m.groups()
            if field == "addrInvalid":
                require(invalid_both(f"io.forward[{lane}].addrInvalid"),
                        f"review newly defined output: {name}")
                policies[name] = ("skip", "DontCare in both FIRRTL designs")
            else:
                policies[name] = (f"forward_valid_d_{lane}", "previous forwarding query valid")
        elif m := re.fullmatch(r"io_store_prefetch_(\d+)_(valid|bits_paddr|bits_vaddr)", name):
            lane, field = m.groups()
            field = field.replace("_", ".")
            require(invalid_both(f"io.store_prefetch[{lane}].{field}"),
                    f"prefetch configuration changed; review {name}")
            policies[name] = ("skip", "prefetch disabled; DontCare in both FIRRTL designs")
        elif m := re.fullmatch(r"io_perf_(\d+)_value", name):
            index = int(m[1])
            require(index < 16, f"review new performance event: {name}")
            # HasPerfEvents adds two unreset RegNext stages. Events 12..15
            # have another RegNext for perf_valid_entry_count in Sbuffer.
            delay = 2 if index < 12 else 3
            policies[name] = (f"history_valid[{delay - 1}]", f"{delay}-cycle post-reset history available")
        else:
            raise ValueError(f"no observation policy for output: {name}")

    lines = [
        "// Generated by formal/sbuffer/make_miter.py; regenerate after changing artifacts.",
        "// Common inputs are arbitrary and shared. Tip TSO enable is tied low.",
        "// No assumptions equate internal state or force uninitialized data to zero.",
        f"// Reset is required on the first {STARTUP_RESET_CYCLES} sampled edges; later resets are allowed.",
        "module miter (",
        ",\n".join("  " + declaration("input wire", n, p) for n, p in inputs.items()),
        ");", "",
    ]
    for side in ("base", "tip"):
        lines += ["  " + declaration("wire", f"{side}_{n}", p) + ";"
                  for n, p in outputs.items()]
        lines.append("")
        lines.append(f"  Sbuffer_{side} dut_{side} (")
        connections = []
        for name, port in ports[side].items():
            signal = "1'b0" if name == TSO else (
                name if port["direction"] == "input" else f"{side}_{name}")
            connections.append(f"    .{name}({signal})")
        lines += [",\n".join(connections), "  );", ""]

    lines += [
        "  reg started = 1'b0;",
        f"  reg [{STARTUP_RESET_CYCLES - 1}:0] startup_reset = '1;",
        "  reg [2:0] history_valid = 3'b000;",
        *[f"  reg forward_valid_d_{lane} = 1'b0;" for lane in lanes],
        "",
        "  always @(posedge clock) begin",
        "    started <= 1'b1;",
        "    startup_reset <= startup_reset >> 1;",
        "    if (|startup_reset) begin",
        "      initial_reset: assume(reset);",
        "    end",
        "    if (reset) begin",
        "      history_valid <= 3'b000;",
        *[f"      forward_valid_d_{lane} <= 1'b0;" for lane in lanes],
        "    end else begin",
        "      history_valid <= {history_valid[1:0], 1'b1};",
        *[f"      forward_valid_d_{lane} <= io_forward_{lane}_valid;" for lane in lanes],
        "    end",
        "  end", "",
        "  // Compare observations before the active edge, after a common reset.",
        "  always @(posedge clock) begin",
        "    if (started && !reset) begin",
    ]
    for name, (qualifier, reason) in policies.items():
        if qualifier == "skip":
            lines.append(f"      // Omitted {name}: {reason}.")
            continue
        if qualifier == "cache_data":
            require(len(outputs[name]["bits"]) == 8 * len(outputs["io_dcache_req_bits_mask"]["bits"]),
                    "cache data/mask width mismatch")
            lines += ["      // Unmasked cache bytes can contain unrelated uninitialized state.",
                      "      if (base_io_dcache_req_valid || tip_io_dcache_req_valid) begin"]
            for byte in range(len(outputs["io_dcache_req_bits_mask"]["bits"])):
                lines += [
                    f"        if (base_io_dcache_req_bits_mask[{byte}] || tip_io_dcache_req_bits_mask[{byte}]) begin",
                    f"          eq_cache_data_byte_{byte}: assert(base_io_dcache_req_bits_data[{8*byte} +: 8] == tip_io_dcache_req_bits_data[{8*byte} +: 8]);",
                    "        end",
                ]
            lines.append("      end")
        else:
            assertion = f"eq_{name}: assert(base_{name} == tip_{name});"
            if qualifier == "always":
                lines.append(f"      {assertion}")
            else:
                lines += [f"      if ({qualifier}) begin", f"        {assertion}", "      end"]
    lines += [
        "    end",
        "  end", "",
        "  // Reachability targets; these add no input assumptions.",
        "  reg seen_store = 1'b0;",
        "  reg seen_request = 1'b0;",
        "  reg seen_flush = 1'b0;",
        "  always @(posedge clock) begin",
        "    if (reset) begin",
        "      seen_store <= 1'b0;",
        "      seen_request <= 1'b0;",
        "      seen_flush <= 1'b0;",
        "    end else if (started) begin",
        "      if (io_in_0_valid && base_io_in_0_ready && io_in_0_bits_vecValid)",
        "        seen_store <= 1'b1;",
        "      if (base_io_dcache_req_valid && io_dcache_req_ready)",
        "        seen_request <= 1'b1;",
        "      if (io_flush_valid)",
        "        seen_flush <= 1'b1;",
        "      accepted_lane0: cover(io_in_0_valid && base_io_in_0_ready && io_in_0_bits_vecValid);",
        "      accepted_both_lanes: cover(io_in_0_valid && base_io_in_0_ready && io_in_0_bits_vecValid && io_in_1_valid && base_io_in_1_ready && io_in_1_bits_vecValid);",
        "      cache_backpressure: cover(base_io_dcache_req_valid && !io_dcache_req_ready);",
        "      cache_request: cover(seen_store && base_io_dcache_req_valid && io_dcache_req_ready);",
        "      drained_after_request: cover(seen_request && base_io_sbempty);",
        "      flush_complete: cover(seen_flush && base_io_flush_empty);",
        # No outstanding-request assumptions yet: this cover only checks a
        # replay input is possible, not that it refers to a legal request.
        "      replay_input: cover(seen_request && io_dcache_replay_resp_valid);",
    ]
    for lane in lanes:
        masks = [f"base_io_forward_{lane}_forwardMask_{byte}"
                 for byte in range(16)]
        require(all(n.removeprefix("base_") in outputs for n in masks),
                "review forwarding width; expected sixteen bytes")
        lines.append(f"      forwarding_lane{lane}: cover(forward_valid_d_{lane} && ("
                     + " || ".join(masks) + "));")
    lines += ["    end", "  end", "endmodule", ""]

    report = ["port\tbase_direction\tbase_width\ttip_direction\ttip_width\tpolicy\tcondition_or_reason"]
    for name, port in tip.items():
        old = base.get(name)
        if name == TSO:
            policy, reason = "tie_low", "tip input connected to 1'b0"
        elif port["direction"] == "input":
            policy, reason = "shared", "same arbitrary top-level input to both DUTs"
        else:
            policy, reason = policies[name]
        report.append("\t".join(map(str, [name, old["direction"] if old else "-",
                                             len(old["bits"]) if old else "-",
                                             port["direction"], len(port["bits"]), policy, reason])))
    (root / "miter.sv").write_text("\n".join(lines))
    (root / "interface.tsv").write_text("\n".join(report) + "\n")
    skipped = sum(q == "skip" for q, _ in policies.values())
    print(f"Interface match: {len(inputs)} common inputs, {len(outputs)} common outputs")
    print(f"Only added port: input {TSO} (1 bit), tied low")
    print(f"Output policies: {len(outputs) - skipped} compared, {skipped} documented DontCare exclusions")
    print(f"Wrote {root / 'interface.tsv'}")
    print(f"Wrote {root / 'miter.sv'}")


if __name__ == "__main__":
    main()
