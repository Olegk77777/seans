#!/bin/bash
# АскБука Поэзии — двойной клик чтобы запустить
cd "$(dirname "$0")"

echo "📖 Запускаю АскБука Поэзии..."
echo ""

# Проверяем Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 не найден! Установи его: brew install python3"
    read -p "Нажми Enter..."
    exit 1
fi

# Проверяем/устанавливаем зависимости
python3 -c "import requests; from bs4 import BeautifulSoup" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "📦 Устанавливаю необходимые библиотеки..."
    pip3 install requests beautifulsoup4 lxml
    echo ""
fi

# Запускаем приложение
python3 app.py
