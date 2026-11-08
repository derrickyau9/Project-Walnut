`timescale 1ns/1ps
module tb_capture_stream;
    parameter integer FRAME_WIDTH=4, FRAME_HEIGHT=3, FIFO_DEPTH=3;
    localparam integer N=FRAME_WIDTH*FRAME_HEIGHT;
    reg clk=0; always #5 clk=~clk;
    reg rst=1, s_valid=0, s_sof=0, s_eol=0, s_eof=0, s_error=0;
    reg [15:0] s_pixel=0;
    reg [31:0] s_frame_id=0;
    wire s_ready;
    reg source_loss=0, abort_frame=0, m_ready=1, status_ready=1;
    wire m_valid,m_sof,m_eol,m_eof,m_error,status_valid,status_good;
    wire [15:0] m_pixel,status_error_mask;
    wire [63:0] status_checksum64;
    wire [31:0] m_frame_id,status_frame_id,status_pixel_count,status_line_count,fifo_level;
    hf_capture_stream #(.FRAME_WIDTH(FRAME_WIDTH),.FRAME_HEIGHT(FRAME_HEIGHT),.FIFO_DEPTH(FIFO_DEPTH)) dut(.*);

    reg random_ready=0;
    reg [31:0] rng=32'h45a319ef;
    integer cycles=0, full_exchanges=0, checked=0, completed=0, accepted=0, emitted=0;
    integer qread=0,qwrite=0,qcount=0;
    reg [51:0] scoreboard[0:8191];
    reg [31:0] ids[0:511], pixel_counts[0:511], line_counts[0:511];
    reg [15:0] masks[0:511];
    reg goods[0:511];
    reg output_stalled=0,status_stalled=0;
    reg [51:0] held_output;
    reg [176:0] held_status;
    reg [63:0] observed_hash=64'd14695981039346656037;
    integer byte_index;
    wire [51:0] output_payload={m_frame_id,m_pixel,m_sof,m_eol,m_eof,m_error};
    wire [176:0] completion_payload={status_frame_id,status_pixel_count,status_line_count,status_error_mask,status_checksum64,status_good};

    always @(negedge clk) begin
        cycles=cycles+1;
        rng={rng[30:0],rng[31]^rng[21]^rng[1]^rng[0]};
        if(random_ready) m_ready=(rng[2:0]!=0 && rng[2:0]!=1) || cycles%11==0;
    end
    always @(posedge clk) begin
        if(rst) begin
            qread=0;qwrite=0;qcount=0;
            output_stalled=0;status_stalled=0;observed_hash=64'd14695981039346656037;
        end else begin
            if(fifo_level>FIFO_DEPTH) $fatal(1,"FIFO exceeded bound");
            if(output_stalled && (!m_valid || output_payload!==held_output)) $fatal(1,"output changed while stalled");
            if(status_stalled && (!status_valid || completion_payload!==held_status)) $fatal(1,"completion changed while stalled");
            if(m_valid && m_ready) begin
                if(qcount==0 || output_payload!==scoreboard[qread]) $fatal(1,"pixel scoreboard mismatch at %0d",emitted);
                qread=(qread+1)%8192;emitted=emitted+1;
            end
            if(s_valid && s_ready) begin
                scoreboard[qwrite]={s_frame_id,s_pixel,s_sof,s_eol,s_eof,s_error};
                qwrite=(qwrite+1)%8192;accepted=accepted+1;
                // Byte-wise scoreboard independent of the DUT's pixel function.
                for(byte_index=0;byte_index<2;byte_index=byte_index+1) begin
                    observed_hash=(observed_hash ^ ((s_pixel >> (byte_index*8)) & 16'hff))*64'd1099511628211;
                end
            end
            case({s_valid&&s_ready,m_valid&&m_ready})
                2'b10:qcount=qcount+1;
                2'b01:qcount=qcount-1;
            endcase
            if(fifo_level==FIFO_DEPTH && s_valid&&s_ready&&m_valid&&m_ready) full_exchanges=full_exchanges+1;
            if(status_valid) begin
                if(qcount!=0 || fifo_level!=0) $fatal(1,"completion preceded data drain");
                if(status_good !== (status_error_mask==0)) $fatal(1,"integrity bit disagrees with errors");
                if(status_checksum64 !== observed_hash) $fatal(1,"accepted-pixel checksum mismatch");
            end
            if(status_valid && status_ready) begin
                ids[completed]=status_frame_id;pixel_counts[completed]=status_pixel_count;
                line_counts[completed]=status_line_count;masks[completed]=status_error_mask;
                goods[completed]=status_good;completed=completed+1;observed_hash=64'd14695981039346656037;
            end
            output_stalled=m_valid&&!m_ready;held_output=output_payload;
            status_stalled=status_valid&&!status_ready;held_status=completion_payload;
        end
    end

    task reset_dut;
        begin
            @(negedge clk);rst=1;s_valid=0;source_loss=0;abort_frame=0;random_ready=0;
            repeat(3) @(negedge clk);
            rst=0;m_ready=1;status_ready=1;
            @(negedge clk);
        end
    endtask
    function [15:0] pixel;
        input integer id,index;
        begin pixel=(id*31+index*13)&16'h3fff;end
    endfunction
    task beat;
        input integer id,index;
        input sof,eol,eof,err,loss;
        input [15:0] value;
        begin
            @(negedge clk);s_valid=1;s_frame_id=id;s_pixel=value;
            s_sof=sof;s_eol=eol;s_eof=eof;s_error=err;source_loss=loss;
            @(posedge clk);
            while(!s_ready) @(posedge clk);
            @(negedge clk);s_valid=0;source_loss=0;
        end
    endtask
    // Variants: 0 normal;1 early EOF;2 missing EOF;3 malformed EOL;
    // 4 orphan;5 extra SOF;6 changed ID;7 pixel range;8 adapter error;9 loss.
    task frame;
        input integer id,variant;
        integer i,limit,word_id;
        reg sof,eol,eof,err,loss;
        reg [15:0] value;
        begin
            limit=variant==1?1:N;
            for(i=0;i<limit;i=i+1) begin
                sof=(i==0 && variant!=4) || (variant==5 && i==1);
                eol=(i%FRAME_WIDTH==FRAME_WIDTH-1) || (variant==3 && i==0);
                eof=(i==limit-1 && variant!=2);
                err=variant==8 && i==limit-1;loss=variant==9 && i==limit-1;
                word_id=id+((variant==6 && i==limit-1)?1:0);
                value=variant==7 && i==limit-1?16'hc001:pixel(id,i);
                beat(word_id,i,sof,eol,eof,err,loss,value);
            end
        end
    endtask
    task expect_status;
        input integer id,expected_pixels,expected_lines;
        input good;
        input [15:0] required_mask;
        integer wait_cycles;
        begin
            wait_cycles=0;
            while(completed<=checked && wait_cycles<10000) begin @(negedge clk);wait_cycles=wait_cycles+1;end
            if(completed<=checked) $fatal(1,"completion timeout id=%0d",id);
            if(ids[checked]!==id || pixel_counts[checked]!==expected_pixels || line_counts[checked]!==expected_lines)
                $fatal(1,"completion counters/id mismatch id=%0d got id=%0d pixels=%0d lines=%0d",id,ids[checked],pixel_counts[checked],line_counts[checked]);
            if(goods[checked]!==good || (masks[checked]&required_mask)!==required_mask)
                $fatal(1,"completion integrity mismatch id=%0d good=%0d mask=%h required=%h",id,goods[checked],masks[checked],required_mask);
            checked=checked+1;
        end
    endtask
    integer i,j,before_count;
    initial begin
        reset_dut();
        // Random downstream stalls preserve every accepted word and marker.
        random_ready=1;
        for(j=1;j<=12;j=j+1) begin frame(j,0);expect_status(j,N,FRAME_HEIGHT,1,0);end
        random_ready=0;m_ready=0;
        fork
            frame(20,0);
            begin repeat(FIFO_DEPTH*3+8) @(negedge clk);m_ready=1;end
        join
        expect_status(20,N,FRAME_HEIGHT,1,0);
        if(N>FIFO_DEPTH && full_exchanges==0) $fatal(1,"full FIFO exchange was not exercised");

        // Completion backpressure is stable; a later loss tags the NEXT frame.
        status_ready=0;frame(21,0);
        wait(status_valid);repeat(3) @(negedge clk);
        if(s_ready) $fatal(1,"input accepted with outstanding completion");
        source_loss=1;@(negedge clk);source_loss=0;
        repeat(3) @(negedge clk);status_ready=1;
        expect_status(21,N,FRAME_HEIGHT,1,0);
        frame(22,0);expect_status(22,N,FRAME_HEIGHT,0,16'h0100);
        frame(23,0);expect_status(23,N,FRAME_HEIGHT,1,0);

        if(N>=4 && FRAME_WIDTH>1) begin
            frame(31,1);expect_status(31,1,0,0,16'h0010);
            frame(32,2);expect_status(32,N,FRAME_HEIGHT,0,16'h0400);
            frame(33,3);expect_status(33,N,FRAME_HEIGHT+1,0,16'h0008);
            frame(34,4);expect_status(34,N,FRAME_HEIGHT,0,16'h0001);
            frame(35,5);expect_status(35,N,FRAME_HEIGHT,0,16'h0002);
            frame(36,6);expect_status(36,N,FRAME_HEIGHT,0,16'h0004);
            frame(37,7);expect_status(37,N,FRAME_HEIGHT,0,16'h0040);
            frame(38,8);expect_status(38,N,FRAME_HEIGHT,0,16'h0080);
            frame(39,9);expect_status(39,N,FRAME_HEIGHT,0,16'h0100);
            frame(40,0);expect_status(40,N,FRAME_HEIGHT,1,0);
            // Abort and loss on the same clock must both enter final integrity.
            beat(41,0,1,0,0,0,0,pixel(41,0));
            @(negedge clk);abort_frame=1;source_loss=1;
            @(negedge clk);abort_frame=0;source_loss=0;
            expect_status(41,1,0,0,16'h0300);
        end

        if(N>FIFO_DEPTH && FRAME_WIDTH>1) begin
            // Adapter loss is observed even when the pixel FIFO cannot accept.
            m_ready=0;
            for(i=0;i<FIFO_DEPTH;i=i+1) beat(50,i,i==0,i%FRAME_WIDTH==FRAME_WIDTH-1,0,0,0,pixel(50,i));
            @(negedge clk);source_loss=1;
            @(negedge clk);source_loss=0;m_ready=1;
            for(i=FIFO_DEPTH;i<N;i=i+1) beat(50,i,0,i%FRAME_WIDTH==FRAME_WIDTH-1,i==N-1,0,0,pixel(50,i));
            expect_status(50,N,FRAME_HEIGHT,0,16'h0100);

            // Intentional abort cancels a held, unaccepted offer. Retraction
            // must not falsely poison the next frame with a protocol error.
            m_ready=0;
            for(i=0;i<FIFO_DEPTH;i=i+1) beat(52,i,i==0,i%FRAME_WIDTH==FRAME_WIDTH-1,0,0,0,pixel(52,i));
            @(negedge clk);s_valid=1;s_frame_id=52;s_pixel=pixel(52,FIFO_DEPTH);
            s_sof=0;s_eol=0;s_eof=0;s_error=0;
            @(posedge clk);if(s_ready) $fatal(1,"expected abort offer stalled");
            @(negedge clk);abort_frame=1;
            @(posedge clk);
            @(negedge clk);abort_frame=0;s_valid=0;m_ready=1;
            expect_status(52,FIFO_DEPTH,FIFO_DEPTH/FRAME_WIDTH,0,16'h0200);
            frame(53,0);expect_status(53,N,FRAME_HEIGHT,1,0);

            // Deliberately violate held-payload rules; accepted payload stays
            // scoreboarding exact, and the frame can never complete good.
            m_ready=0;
            for(i=0;i<FIFO_DEPTH;i=i+1) beat(51,i,i==0,i%FRAME_WIDTH==FRAME_WIDTH-1,0,0,0,pixel(51,i));
            @(negedge clk);s_valid=1;s_frame_id=51;s_pixel=pixel(51,FIFO_DEPTH);
            s_sof=0;s_eol=FIFO_DEPTH%FRAME_WIDTH==FRAME_WIDTH-1;s_eof=0;s_error=0;
            @(posedge clk);if(s_ready) $fatal(1,"expected full-FIFO stall");
            @(negedge clk);s_pixel=s_pixel+1;
            @(posedge clk);
            @(negedge clk);m_ready=1;
            @(posedge clk);while(!s_ready) @(posedge clk);
            @(negedge clk);s_valid=0;
            for(i=FIFO_DEPTH+1;i<N;i=i+1) beat(51,i,0,i%FRAME_WIDTH==FRAME_WIDTH-1,i==N-1,0,0,pixel(51,i));
            expect_status(51,N,FRAME_HEIGHT,0,16'h0800);
        end

        // Reset invalidates queued provisional work; it creates no completion.
        before_count=completed;m_ready=0;
        beat(60,0,1,FRAME_WIDTH==1,0,0,0,pixel(60,0));
        reset_dut();repeat(4) @(negedge clk);
        if(completed!=before_count || status_valid || m_valid) $fatal(1,"reset fabricated completion/data");
        frame(61,0);expect_status(61,N,FRAME_HEIGHT,1,0);
        status_ready=0;frame(62,0);wait(status_valid);
        before_count=completed;reset_dut();repeat(3) @(negedge clk);
        if(completed!=before_count || status_valid) $fatal(1,"reset retained pending completion");
        frame(63,0);expect_status(63,N,FRAME_HEIGHT,1,0);
        if(qcount!=0 || checked!=completed) $fatal(1,"unconsumed scoreboard/status");
        $display("HF_RTL_PASS width=%0d height=%0d depth=%0d completions=%0d accepted=%0d emitted=%0d full_exchanges=%0d",FRAME_WIDTH,FRAME_HEIGHT,FIFO_DEPTH,completed,accepted,emitted,full_exchanges);
        $finish;
    end
    initial begin #3000000;$fatal(1,"global simulation timeout");end
endmodule
