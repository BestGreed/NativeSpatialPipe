#pragma once
#include <stddef.h>
#include <stdint.h>
typedef struct NspDecoder NspDecoder;
typedef struct NspOwnedFrame NspOwnedFrame;
typedef struct {uint32_t id,channel;} NspObject;
typedef struct {uint32_t id,flags;uint64_t sample_pos;double pos[3],size[3];float gain_db;uint32_t ramp;} NspEvent;
typedef struct {const int32_t* pcm;const uint8_t* labels;const NspObject* objects;const NspEvent* events;int64_t pts;uint32_t rate,samples,channels,object_count,event_count,reserved;} NspFrame;
// A frame and all pointers remain valid until pop/reset/destroy. No PCM ownership crosses ABI.
#ifdef __cplusplus
extern "C" {
#endif
NspDecoder* nsp_decoder_create(uint32_t codec);
void nsp_decoder_destroy(NspDecoder*);
int32_t nsp_decoder_reset(NspDecoder*);
int32_t nsp_decoder_push(NspDecoder*,const uint8_t*,size_t,int64_t);
int32_t nsp_decoder_front(NspDecoder*,NspFrame*);
int32_t nsp_decoder_pop(NspDecoder*);
size_t nsp_decoder_error(const NspDecoder*,uint8_t*,size_t);
NspOwnedFrame* nsp_decoder_take(NspDecoder*);
void nsp_frame_release(NspOwnedFrame*);
int32_t nsp_frame_view(const NspOwnedFrame*,NspFrame*);
uint64_t nsp_frame_start(const NspOwnedFrame*);
#ifdef __cplusplus
}
#endif
