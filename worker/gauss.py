import time

MAX_N = 300
MAX_SECONDS = 60


class TaskLimitError(Exception):
    pass


class TaskCancelled(Exception):
    pass


class SingularMatrixError(Exception):
    pass


def validate(a, b):
    n = len(a)
    if n < 1 or n > MAX_N:
        raise TaskLimitError(f"Кількість невідомих має бути від 1 до {MAX_N}")
    if len(b) != n:
        raise TaskLimitError("Розмір вектора b не збігається з розміром матриці")
    for row in a:
        if len(row) != n:
            raise TaskLimitError("Матриця A має бути квадратною")


def solve(a, b, on_progress=None, is_cancelled=None):
    validate(a, b)
    n = len(a)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    started = time.monotonic()

    for k in range(n):
        if is_cancelled and is_cancelled():
            raise TaskCancelled()
        if time.monotonic() - started > MAX_SECONDS:
            raise TaskLimitError("Перевищено максимальний час виконання")

        pivot = max(range(k, n), key=lambda i: abs(m[i][k]))
        if abs(m[pivot][k]) < 1e-12:
            raise SingularMatrixError("Матриця вироджена, єдиного розв'язку немає")
        m[k], m[pivot] = m[pivot], m[k]

        for i in range(k + 1, n):
            factor = m[i][k] / m[k][k]
            for j in range(k, n + 1):
                m[i][j] -= factor * m[k][j]

        if on_progress:
            on_progress(int((k + 1) / n * 100))

    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = sum(m[i][j] * x[j] for j in range(i + 1, n))
        x[i] = (m[i][n] - s) / m[i][i]
    return x