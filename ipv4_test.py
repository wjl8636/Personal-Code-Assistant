from ipv4 import is_valid_ipv4

CASES = [
    ("0.0.0.0", True),
    ("255.255.255.255", True),
    ("192.168.1.1", True),
    ("256.1.1.1", False),
    ("1.2.3", False),
    ("1.2.3.4.5", False),
    ("01.2.3.4", False),
    ("1.2.3.a", False),
    ("", False),
    ("1.2.3.-1", False),
    (" 1.2.3.4", False),
    ("1.2.3.4 ", False),
    ("\u0661\u0662\u0663.1.1.1", False),  # arabic-indic digits
    ("\u00b2.1.1.1", False),  # superscript, isdigit() but not int()-able
    ("\uff11.1.1.1", False),  # fullwidth digits
    ("1.2.3.4\n", False),
]

failed = 0
for text, expected in CASES:
    got = is_valid_ipv4(text)
    status = "ok" if got == expected else "FAIL"
    if got != expected:
        failed += 1
    print(f"{status:4} {text!r:20} expected={expected} got={got}")

print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
