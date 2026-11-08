`timescale 1ns/1ps
// File-based logical pixel verification only; not a sensor or DMA interface.
module tb_dng_roundtrip;
    localparam W = 32;
    localparam H = 24;
    localparam N = W * H;
    reg clk = 0;
    always #5 clk = !clk;
    reg rst = 1;
    reg s_valid = 0;
    wire s_ready;
    reg [15:0] s_pixel = 0;
    reg [31:0] s_frame_id = 42;
    reg s_sof = 0, s_eol = 0, s_eof = 0, s_error = 0;
    wire m_valid;
    reg m_ready = 0;
    wire [15:0] m_pixel;
    wire [31:0] m_frame_id;
    wire m_sof, m_eol, m_eof, m_error;
    wire status_valid, status_good;
    wire [31:0] status_frame_id, status_pixel_count, status_line_count, fifo_level;
    wire [15:0] status_error_mask;
    wire [63:0] status_checksum64;
    reg status_ready = 0;
    reg [15:0] stimulus [0:N-1];
    string input_path, output_path;
    integer output_file, index, cycle = 0, emitted = 0, corrupt = 0;

    hf_capture_stream #(.FRAME_WIDTH(W), .FRAME_HEIGHT(H), .FIFO_DEPTH(3)) dut (
        .clk(clk), .rst(rst), .s_valid(s_valid), .s_ready(s_ready),
        .s_pixel(s_pixel), .s_frame_id(s_frame_id), .s_sof(s_sof), .s_eol(s_eol), .s_eof(s_eof), .s_error(s_error),
        .source_loss(1'b0), .abort_frame(1'b0), .m_valid(m_valid), .m_ready(m_ready), .m_pixel(m_pixel),
        .m_frame_id(m_frame_id), .m_sof(m_sof), .m_eol(m_eol), .m_eof(m_eof), .m_error(m_error),
        .status_valid(status_valid), .status_ready(status_ready), .status_good(status_good),
        .status_frame_id(status_frame_id), .status_pixel_count(status_pixel_count),
        .status_line_count(status_line_count), .status_error_mask(status_error_mask),
        .status_checksum64(status_checksum64), .fifo_level(fifo_level)
    );

    always @(negedge clk) begin
        if (rst) begin cycle <= 0; m_ready <= 0; end
        else begin cycle <= cycle + 1; m_ready <= cycle % 7 >= 3; end
    end
    always @(posedge clk) begin
        if (!rst && m_valid && m_ready) begin
            $fdisplay(output_file, "P %0d %0d %0d %0d %0d %0d", m_frame_id, m_pixel, m_sof, m_eol, m_eof, m_error);
            emitted = emitted + 1;
        end
    end
    initial begin
        if (!$value$plusargs("INPUT=%s", input_path) || !$value$plusargs("OUTPUT=%s", output_path))
            $fatal(1, "INPUT and OUTPUT required");
        if ($value$plusargs("CORRUPT=%d", corrupt)) begin end
        $readmemh(input_path, stimulus);
        output_file = $fopen(output_path, "w");
        if (!output_file) $fatal(1, "cannot open trace");
        repeat (4) @(negedge clk);
        rst = 0;
        for (index = 0; index < N; index = index + 1) begin
            @(negedge clk);
            s_valid = 1;
            s_pixel = stimulus[index];
            s_sof = index == 0;
            s_eol = index % W == W - 1;
            s_eof = index == N - 1;
            s_error = corrupt != 0 && index == W + 3;
            @(posedge clk);
            while (!s_ready) @(posedge clk);
            @(negedge clk);
            s_valid = 0;
        end
        wait (status_valid);
        #1;
        if (emitted != N || status_pixel_count != N || status_line_count != H || status_frame_id != 42)
            $fatal(1, "capture counts or ID mismatch");
        if ((!corrupt && (!status_good || status_error_mask != 0)) ||
            (corrupt && (status_good || status_error_mask != 16'h0080)))
            $fatal(1, "completion validity mismatch");
        $fdisplay(output_file, "S %0d %0d %0d %0d %0d %0d", status_good, status_frame_id,
                  status_pixel_count, status_line_count, status_error_mask, status_checksum64);
        $fclose(output_file);
        $display("PASS DNG_STREAM pixels=%0d errors=%0d", emitted, status_error_mask);
        status_ready = 1;
        @(posedge clk);
        $finish;
    end
    initial begin #200000; $fatal(1, "testbench watchdog expired"); end
endmodule
