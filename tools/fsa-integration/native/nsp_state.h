#ifndef FSA_NSP_STATE_H
#define FSA_NSP_STATE_H
enum { NSP_RUNNING=0, NSP_STOPPED=10, NSP_BUSY=11, NSP_PREFLIGHT=12, NSP_UNKNOWN=20 };
/* Sonic alone never means NSp. An unknown observation keeps control locked. */
static int nsp_verified(int observation,int sonic,int fault) {
    return observation==NSP_RUNNING && sonic && !fault;
}
static int nsp_blocks_rules(int observation) {return observation!=NSP_STOPPED;}
#endif
