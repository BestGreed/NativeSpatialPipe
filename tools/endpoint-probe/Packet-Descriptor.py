"""Expand a uniform-frame wire descriptor into the unchanged recorder contract."""
class Descriptors:
    def __init__(self):self.cache={}
    def expand(self,record):
        if 'framesUniform' not in record:return record
        if 'frames' in record:raise ValueError('Ambiguous frame descriptor')
        count=record['count'];frames=record.pop('framesUniform')
        if type(count) is not int or type(frames) is not int or not 1<=count<=160 or not 1<=frames<=4096:raise ValueError('Invalid uniform frame descriptor')
        key=count,frames
        if key not in self.cache:
            if len(self.cache)>=64:
                record['frames']=[frames]*count;return record
            self.cache[key]=[frames]*count
        record['frames']=self.cache[key];return record
