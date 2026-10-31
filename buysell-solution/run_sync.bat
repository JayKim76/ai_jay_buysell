@echo off
chcp 65001 > nul
cd /d %~dp0
python app.py sync-orders
python app.py sync-products
python app.py evaluate
