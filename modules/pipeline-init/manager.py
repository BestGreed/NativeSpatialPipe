"""Compatibility status for public endpoint preparation without extra enumeration."""


def status():
    return {'live': False, 'formats': [], 'supported': False}


def stop():
    return {**status(), 'stopped': True}


if __name__ == '__main__':
    import json

    print(json.dumps({'error': 'Extra AudioSrv enumeration is outside public support.'}))
    raise SystemExit(1)
