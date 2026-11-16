"""Versioned front-pipeline contract; no Windows side effects."""
import struct
FORMATS={
 'WindowsSonic':'b53d940c-b846-4831-9f76-d102b9b725a0',
 'DolbyAtmosForHeadphones':'1459ac38-3875-49bf-bb59-0fe80f4d395d',
 'DolbyAtmosForSpeakers':'4c81e564-c8ef-4ad9-9f2c-9ef995533790',
 'DolbyAtmosForHomeTheater':'a289735d-fa3e-4e35-9d7d-b6f896acb2e7',
 'DTSHeadphoneX':'4444acb0-8dc0-4c2c-a0d8-2c76db470f86',
 'DTSXUltra':'adafd3c6-ac4c-404a-836a-9615e9060564',
 'DTSXForHomeTheater':'10201b4a-3322-4967-bf40-2caa9bafca44'}
NAMES=list(FORMATS)
GROUPS={'headphones':['DolbyAtmosForHeadphones','DTSHeadphoneX'],
        'speakers':['DolbyAtmosForSpeakers','DTSXUltra']}
LICENSE_INDEX={'DolbyAtmosForHeadphones':2,'DolbyAtmosForSpeakers':3,'DTSHeadphoneX':4,'DTSXUltra':5}
MAP_NAME=r'Local\NSp-PipelineInit-v1'
MAP_SIZE=2048
# seq, magic, version, state, pid, flags, heartbeat-ms, creation-FILETIME,
# audiosrv-pid, format-bitmask, UTF16 endpoint[512]; remaining bytes reserved.
HEADER=struct.Struct('<6I2Q2I')
MAGIC=0x4950534e
def selected_formats(groups):
 return [name for group in groups for name in GROUPS[group]]
def mask_for(names):return sum(1<<NAMES.index(n) for n in set(names))
def eligible(tag,endpoint,now_ms,process_created,persistent=False):
 return bool(tag and tag['state']==2 and tag['flags']&1 and
  (not persistent or tag['flags']&2) and tag['created']==process_created and
  tag['endpoint'].lower()==endpoint.lower() and 0<=now_ms-tag['heartbeat']<=3000)
def decode_tag(data):
 seq,magic,version,state,pid,flags,heartbeat,created,service,mask=HEADER.unpack_from(data)
 if seq&1 or magic!=MAGIC or version!=1:return None
 return dict(sequence=seq,state=state,pid=pid,flags=flags,heartbeat=heartbeat,
             created=created,servicePid=service,mask=mask,
             endpoint=data[48:1072].decode('utf-16-le').split('\0',1)[0])
