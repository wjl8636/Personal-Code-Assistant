def is_valid_ipv4(s: str) -> bool:
    if not isinstance(s, str):
        return False
    parts = s.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        # isdigit() also accepts non-ASCII digits such as "١٢٣" and "²",
        # so require ASCII before converting to int.
        if not part.isascii() or not part.isdigit():
            return False
        if not 1 <= len(part) <= 3:
            return False
        if len(part) > 1 and part[0] == "0":
            return False
        if int(part) > 255:
            return False
    return True
