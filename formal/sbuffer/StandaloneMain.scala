package lec

import java.nio.charset.StandardCharsets
import java.nio.file.{Files, Paths}
import chisel3.{Element, RawModule}
import chisel3.reflect.DataMirror
import top.{ArgParser, Generator}
import xiangshan.{XSCoreParamsKey, XSTileKey}
import xiangshan.mem.{LoadQueueRAR, Sbuffer}

/** Elaboration only: leave the DUT's reset, TSO input, state and assertions intact. */
object StandaloneMain extends App {
  require(args.nonEmpty && Set("Sbuffer", "LoadQueueRAR").contains(args.head),
    "usage: lec.StandaloneMain {Sbuffer|LoadQueueRAR} [XiangShan/stage options]")

  val (config, stageOpts, firtoolOpts) = ArgParser.parse(args.tail ++ Array(
    "--fpga-platform", "--disable-perf", "--disable-alwaysdb", "--disable-xmr", "--disable-all"
  ))
  // Small modules do not have XSTile's parameter scope; supply it explicitly.
  implicit val parameters: org.chipsalliance.cde.config.Parameters = config.alterPartial {
    case XSCoreParamsKey => config(XSTileKey).head
  }
  require(!parameters(xiangshan.DebugOptionsKey).EnableDifftest,
    "standalone formal elaboration requires difftest disabled")

  var dut: RawModule = null
  Generator.execute(stageOpts, {
    dut = args.head match {
      case "Sbuffer" => new Sbuffer
      case "LoadQueueRAR" => new LoadQueueRAR
    }
    dut
  }, firtoolOpts)

  val targetIndex = stageOpts.indexOf("--target-dir")
  require(targetIndex >= 0 && targetIndex + 1 < stageOpts.length, "supply --target-dir")
  val target = Paths.get(stageOpts(targetIndex + 1))
  Files.createDirectories(target)
  // Some widths are inferred only by firtool; preserve UnknownWidth here and
  // check the authoritative lowered port widths before constructing a miter.
  val ports = DataMirror.fullModulePorts(dut).collect {
    case (name, data: Element) =>
      s"$name\t${DataMirror.directionOf(data)}\t${DataMirror.widthOf(data)}"
  }
  Files.write(target.resolve("ports.tsv"),
    ("name\tdirection\tchisel_width\n" + ports.mkString("\n") + "\n")
      .getBytes(StandardCharsets.UTF_8))
  Files.write(target.resolve("parameters.txt"),
    ("module=" + args.head + "\ncore=" + parameters(XSCoreParamsKey).toString +
      "\nsoc=" + parameters(system.SoCParamsKey).toString +
      "\ndebug=" + parameters(xiangshan.DebugOptionsKey).toString + "\n")
      .getBytes(StandardCharsets.UTF_8))
}
