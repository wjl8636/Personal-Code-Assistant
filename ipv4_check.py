def is_valid_ipv4(s):
    parts = s.split('.')
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit() or (len(p) > 1 and p[0] == '0'):
            return False
        if int(p) > 255:
            return False
    return True


if __name__ == '__main__':
    tests = ['192.168.0.1', '255.255.255.255', '256.1.1.1', '1.2.3', '1.2.3.4.5',
             '01.2.3.4', '1.2.3.-4', 'a.b.c.d', '0.0.0.0', '1.2.3.04', '']
    for t in tests:
        print(repr(t), is_valid_ipv4(t))
