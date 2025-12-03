import re

# Файл для проверки
FILE_PATH = "main.py"

with open(FILE_PATH, "r", encoding="utf-8") as f:
    lines = f.readlines()

fixed_lines = []
for line in lines:
    # Заменяем все табуляции на 4 пробела
    fixed_line = line.replace("\t", "    ")
    # Убираем лишние пробелы в начале и потом добавляем ровно 4 на уровень
    leading_spaces = len(re.match(r" *", fixed_line).group(0))
    # Оставляем все пробелы как есть, просто заменяем табы на пробелы
    fixed_lines.append(fixed_line)

# Перезаписываем файл
with open(FILE_PATH, "w", encoding="utf-8") as f:
    f.writelines(fixed_lines)

print(f"[✔] Все табуляции заменены на пробелы в {FILE_PATH}")
