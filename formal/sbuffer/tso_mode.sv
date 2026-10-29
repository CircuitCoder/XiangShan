module tso_mode_check(input clock, input reset);
  wire tsoActive, tsoTransitioning;
  tso_mode dut (
    .clock(clock), .reset(reset),
    .tsoActive(tsoActive), .tsoTransitioning(tsoTransitioning)
  );

  reg started = 1'b0;
  always @(posedge clock) begin
    started <= 1'b1;
    if (!started)
      initial_reset: assume(reset);
    if (started) begin
      inactive: assert(!tsoActive);
      no_transition: assert(!tsoTransitioning);
    end
  end
endmodule
