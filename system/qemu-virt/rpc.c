/* Fixed-fixture guest smoke, not a general JSON SDK. Input is our own daemon. */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#define RID "26110500-0000-4000-8000-000000000002"
#define MAX_REPLY 262145
static char response[MAX_REPLY];
static FILE *input, *output;
static pid_t daemon_pid;
static int next_id;

static int launch(void) {
    int send[2], receive[2];
    if (pipe(send) || pipe(receive)) return 0;
    daemon_pid=fork();
    if (daemon_pid==-1) return 0;
    if (daemon_pid==0) {
        if (dup2(send[0],0)==-1 || dup2(receive[1],1)==-1) _exit(126);
        close(send[0]); close(send[1]); close(receive[0]); close(receive[1]);
        execl("/bin/hf-camerad","hf-camerad","--data-dir","/camera",NULL);
        _exit(127);
    }
    close(send[0]); close(receive[1]);
    input=fdopen(send[1],"w"); output=fdopen(receive[0],"r");
    if (!input || !output) return 0;
    next_id=0;
    return 1;
}

static int rpc(const char *method, const char *params) {
    char expected[64];
    ++next_id;
    if (fprintf(input,"{\"id\":%d,\"method\":\"%s\",\"params\":%s}\n",next_id,method,params)<0 || fflush(input)) return 0;
    if (!fgets(response,sizeof response,output) || !strchr(response,'\n')) return 0;
    snprintf(expected,sizeof expected,"\"id\":%d,",next_id);
    const char *result=strstr(response,"\"result\":");
    const char *error=strstr(response,"\"error\":");
    /* Capture records include a nested error:null; only an envelope error
       before result denotes RPC failure in this fixed, trusted fixture. */
    return strstr(response,expected) && result && (!error || error>result);
}

static int string_field(const char *field, char *destination, size_t capacity) {
    char marker[64]; snprintf(marker,sizeof marker,"\"%s\":\"",field);
    const char *start=strstr(response,marker);
    if (!start) return 0;
    start+=strlen(marker);
    const char *end=strchr(start,'"');
    if (!end || end==start || (size_t)(end-start)>=capacity) return 0;
    for (const char *p=start;p<end;++p) if (!isalnum((unsigned char)*p) && *p!='_' && *p!='-') return 0;
    memcpy(destination,start,(size_t)(end-start)); destination[end-start]=0;
    return 1;
}

static int close_daemon(void) {
    if (!rpc("close","{}") || !strstr(response,"\"closed\":true")) return 0;
    fclose(input); fclose(output);
    int status;
    while (waitpid(daemon_pid,&status,0)==-1) if (errno!=EINTR) return 0;
    return WIFEXITED(status) && WEXITSTATUS(status)==0;
}

int hf_rpc_capture(void) {
    char token[129], frame[129], restored[129], params[2048];
    if (!launch() || !rpc("acquire","{\"owner\":\"qemu-virt-smoke\"}") || !string_field("token",token,sizeof token)) return 0;
    const char *position=strstr(response,"\"epoch\":");
    if (!position) return 0;
    char *end;
    errno=0;
    unsigned long long epoch=strtoull(position+8,&end,10);
    if (errno || !epoch || (*end!=',' && *end!='}')) return 0;
    printf("HF_QEMU_REQUEST " RID "\n"); /* Retained before the only capture send. */
    snprintf(params,sizeof params,"{\"request_id\":\"" RID "\",\"token\":\"%s\",\"control_epoch\":%llu,"
        "\"sensor_mode\":\"sim-small\",\"requested_exposure_us\":12500,\"requested_analogue_gain\":1.5,"
        "\"output_format\":\"dng\",\"simulation_scene\":\"scene\"}",token,epoch);
    if (!rpc("capture",params)) return 0;
    int complete=0;
    for (int i=0;i<500;++i) {
        if (!rpc("query_capture","{\"request_id\":\"" RID "\"}")) return 0;
        if (strstr(response,"\"status\":\"complete\"")) { complete=1; break; }
        if (strstr(response,"\"status\":\"failed\"") || strstr(response,"\"status\":\"cancelled\"") || strstr(response,"\"status\":\"unknown\"")) return 0;
        usleep(20000);
    }
    if (!complete || !string_field("frame_id",frame,sizeof frame) || !close_daemon()) return 0;
    if (!launch() || !rpc("query_capture","{\"request_id\":\"" RID "\"}") || !strstr(response,"\"status\":\"complete\"")
        || !string_field("frame_id",restored,sizeof restored) || strcmp(frame,restored)) return 0;
    if (!rpc("list_captures","{\"limit\":1}") || !strstr(response,RID)) return 0;
    if (!rpc("get_capture_asset","{\"request_id\":\"" RID "\"}") || !strstr(response,RID)) return 0;
    if (!close_daemon()) return 0;
    printf("HF_QEMU_RPC_RESTART_OK same-request same-frame synthetic-only\n");
    return 1;
}
