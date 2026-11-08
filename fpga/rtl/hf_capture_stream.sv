`timescale 1ns/1ps
// Logical already-decoded pixel capture reference. No sensor PHY, packing or CDC.
module hf_capture_stream #(
    parameter integer FRAME_WIDTH = 4,
    parameter integer FRAME_HEIGHT = 2,
    parameter integer PIXEL_BITS = 14,
    parameter integer FIFO_DEPTH = 4
) (
    input wire clk,
    input wire rst,
    input wire s_valid,
    output wire s_ready,
    input wire [15:0] s_pixel,
    input wire [31:0] s_frame_id,
    input wire s_sof,
    input wire s_eol,
    input wire s_eof,
    input wire s_error,
    input wire source_loss,
    input wire abort_frame,
    output wire m_valid,
    input wire m_ready,
    output wire [15:0] m_pixel,
    output wire [31:0] m_frame_id,
    output wire m_sof,
    output wire m_eol,
    output wire m_eof,
    output wire m_error,
    output wire status_valid,
    input wire status_ready,
    output wire status_good,
    output reg [31:0] status_frame_id,
    output reg [31:0] status_pixel_count,
    output reg [31:0] status_line_count,
    output reg [15:0] status_error_mask,
    output reg [63:0] status_checksum64,
    output wire [31:0] fifo_level
);
    localparam integer PTR_BITS = FIFO_DEPTH <= 1 ? 1 : $clog2(FIFO_DEPTH);
    localparam integer COUNT_BITS = $clog2(FIFO_DEPTH + 1);
    localparam [31:0] EXPECTED_PIXELS = FRAME_WIDTH * FRAME_HEIGHT;
    localparam [15:0] ERR_NO_SOF = 16'h0001,
                      ERR_EXTRA_SOF = 16'h0002,
                      ERR_FRAME_ID = 16'h0004,
                      ERR_LINE_LENGTH = 16'h0008,
                      ERR_FRAME_LENGTH = 16'h0010,
                      ERR_MARKERS = 16'h0020,
                      ERR_RANGE = 16'h0040,
                      ERR_INPUT = 16'h0080,
                      ERR_OVERFLOW = 16'h0100,
                      ERR_ABORT = 16'h0200,
                      ERR_MISSING_EOF = 16'h0400,
                      ERR_PROTOCOL = 16'h0800;

    reg [51:0] memory [0:FIFO_DEPTH-1];
    reg [PTR_BITS-1:0] read_ptr, write_ptr;
    reg [COUNT_BITS-1:0] count;
    reg active, closing;
    reg [31:0] frame_id, pixels, lines, line_pixels;
    reg [15:0] errors, pending_errors;
    localparam [63:0] FNV_OFFSET = 64'd14695981039346656037;
    reg [63:0] checksum64;
    reg stalled;
    reg [51:0] held_payload;
    wire [51:0] offered_payload = {s_frame_id,s_pixel,s_sof,s_eol,s_eof,s_error};
    wire pop = m_valid && m_ready;
    wire push = s_valid && s_ready;
    wire protocol_violation = stalled && !abort_frame && (!s_valid || offered_payload != held_payload);
    wire [15:0] sideband_flags = (source_loss ? ERR_OVERFLOW : 16'h0) |
                                     (protocol_violation ? ERR_PROTOCOL : 16'h0);

    assign m_valid = !rst && count != 0;
    assign {m_frame_id,m_pixel,m_sof,m_eol,m_eof,m_error} = memory[read_ptr];
    // A stalled valid offer is normal. Full FIFO may pop and push on one clock.
    assign s_ready = !rst && !closing && !abort_frame && (count < FIFO_DEPTH || pop);
    assign status_valid = !rst && closing && count == 0;
    assign status_good = status_valid && status_error_mask == 0;
    assign fifo_level = count;

    // Work variables intentionally use blocking assignments to include final-beat
    // errors in the completion record, rather than last clock's sticky mask.
    reg [15:0] work_errors;
    reg [31:0] work_pixels, work_lines, work_line_pixels, work_id;
    reg [63:0] work_checksum64;
    // Logical buffer handoff checksum, not a sensor-link CRC. Each accepted
    // container contributes its low byte followed by its high byte, modulo 2^64.
    function [63:0] fnv_pixel;
        input [63:0] hash;
        input [15:0] value;
        reg [63:0] h;
        begin
            h = (hash ^ {56'b0,value[7:0]}) * 64'd1099511628211;
            fnv_pixel = (h ^ {56'b0,value[15:8]}) * 64'd1099511628211;
        end
    endfunction

    initial begin
        if (FRAME_WIDTH < 1 || FRAME_HEIGHT < 1 || FRAME_WIDTH > 65535 || FRAME_HEIGHT > 65535)
            $fatal(1,"frame dimensions must be 1..65535");
        if (FIFO_DEPTH < 1 || PIXEL_BITS < 1 || PIXEL_BITS > 16)
            $fatal(1,"FIFO_DEPTH must be positive and PIXEL_BITS must be 1..16");
    end

    always @(posedge clk) begin
        if (rst) begin
            read_ptr <= 0; write_ptr <= 0; count <= 0;
            active <= 0; closing <= 0;
            frame_id <= 0; pixels <= 0; lines <= 0; line_pixels <= 0;
            errors <= 0; pending_errors <= 0; checksum64 <= FNV_OFFSET;
            stalled <= 0; held_payload <= 0;
            status_frame_id <= 0; status_pixel_count <= 0;
            status_line_count <= 0; status_error_mask <= 0; status_checksum64 <= 0;
        end else begin
            if (push) begin
                memory[write_ptr] <= offered_payload;
                if (write_ptr == FIFO_DEPTH-1) write_ptr <= 0;
                else write_ptr <= write_ptr + 1'b1;
            end
            if (pop) begin
                if (read_ptr == FIFO_DEPTH-1) read_ptr <= 0;
                else read_ptr <= read_ptr + 1'b1;
            end
            case ({push,pop})
                2'b10: count <= count + 1'b1;
                2'b01: count <= count - 1'b1;
                default: count <= count;
            endcase

            // Explicit abort cancels an unaccepted held offer as well as the frame.
            if (abort_frame) stalled <= 0;
            else if (s_valid && !s_ready) begin
                if (!stalled) held_payload <= offered_payload;
                stalled <= 1;
            end else stalled <= 0;

            // Loss with no active frame belongs conservatively to the next one.
            // Once closing, the held completion is immutable; later loss is next.
            if (!active && !push) pending_errors <= pending_errors | sideband_flags;
            if (status_valid && status_ready) closing <= 0;

            if (active) begin
                errors <= errors | sideband_flags;
                if (abort_frame) begin
                    active <= 0; closing <= 1;
                    status_frame_id <= frame_id;
                    status_pixel_count <= pixels;
                    status_line_count <= lines;
                    status_error_mask <= errors | sideband_flags | ERR_ABORT | ERR_FRAME_LENGTH;
                    status_checksum64 <= checksum64;
                end
            end

            if (push) begin
                if (!active) begin
                    work_id = s_frame_id;
                    work_pixels = 0; work_lines = 0; work_line_pixels = 0;
                    work_errors = pending_errors | sideband_flags;
                    work_checksum64 = FNV_OFFSET;
                    pending_errors <= 0;
                    if (!s_sof) work_errors = work_errors | ERR_NO_SOF;
                end else begin
                    work_id = frame_id;
                    work_pixels = pixels; work_lines = lines; work_line_pixels = line_pixels;
                    work_errors = errors | sideband_flags;
                    work_checksum64 = checksum64;
                    if (s_sof) work_errors = work_errors | ERR_EXTRA_SOF;
                    if (s_frame_id != frame_id) work_errors = work_errors | ERR_FRAME_ID;
                end
                work_checksum64 = fnv_pixel(work_checksum64, s_pixel);
                checksum64 <= work_checksum64;
                work_pixels = work_pixels + 1'b1;
                work_line_pixels = work_line_pixels + 1'b1;
                if ((s_pixel >> PIXEL_BITS) != 0) work_errors = work_errors | ERR_RANGE;
                if (s_error) work_errors = work_errors | ERR_INPUT;
                if (s_eol) begin
                    if (work_line_pixels != FRAME_WIDTH) work_errors = work_errors | ERR_LINE_LENGTH;
                    work_lines = work_lines + 1'b1;
                    work_line_pixels = 0;
                end else if (work_line_pixels >= FRAME_WIDTH)
                    work_errors = work_errors | ERR_LINE_LENGTH;
                if (work_lines > FRAME_HEIGHT) work_errors = work_errors | ERR_FRAME_LENGTH;

                frame_id <= work_id; pixels <= work_pixels;
                lines <= work_lines; line_pixels <= work_line_pixels;
                active <= 1; errors <= work_errors;
                // Malformed frames cannot grow without bound while EOF is absent.
                if (s_eof || work_pixels >= EXPECTED_PIXELS) begin
                    if (!s_eof) work_errors = work_errors | ERR_MISSING_EOF;
                    if (!s_eol) work_errors = work_errors | ERR_MARKERS;
                    if (work_pixels != EXPECTED_PIXELS || work_lines != FRAME_HEIGHT)
                        work_errors = work_errors | ERR_FRAME_LENGTH;
                    active <= 0; closing <= 1; errors <= work_errors;
                    status_frame_id <= work_id;
                    status_pixel_count <= work_pixels;
                    status_line_count <= work_lines;
                    status_error_mask <= work_errors;
                    status_checksum64 <= work_checksum64;
                end
            end
        end
    end
endmodule
