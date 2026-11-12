@echo off
powershell -NoProfile -Command "w32tm /resync /force"
