def to_roman(num: int) -> str:
    if not isinstance(num, int) or isinstance(num, bool):
        raise TypeError("num must be an int")
    if not 0 < num < 4000:
        raise ValueError("num must be between 1 and 3999")

    values = (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    )

    out = []
    for value, symbol in values:
        count, num = divmod(num, value)
        out.append(symbol * count)
    return "".join(out)
