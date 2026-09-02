def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]
    close_change = abs(df['close'].pct_change(1))
    df[factor_name] = close_change.rolling(n).max()

    return df
