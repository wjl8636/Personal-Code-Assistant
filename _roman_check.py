from roman import to_roman

cases = {58: "LVIII"}
for n, want in cases.items():
    got = to_roman(n)
    assert got == want, (n, got, want)
    print(n, got)

for bad in (0, -5, 4000, True, 1.5):
    try:
        to_roman(bad)
        print("NO ERROR for", repr(bad))
    except (TypeError, ValueError) as e:
        print("rejected", repr(bad), type(e).__name__)

print("all passed")
