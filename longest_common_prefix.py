def longest_common_prefix(strs):
    if not strs:
        return ""
    shortest = min(strs, key=len)
    for i, ch in enumerate(shortest):
        for s in strs:
            if s[i] != ch:
                return shortest[:i]
    return shortest


if __name__ == "__main__":
    samples = [
        ["flower", "flow", "flight"],
        ["interview", "internet", "interval"],
        ["dog", "racecar", "car"],
        [],
        ["single"],
    ]
    for s in samples:
        print(f"{s} -> {longest_common_prefix(s)!r}")
