@echo off
cd /d d:\Liu\ai_platform_code\phase2_v3
e:\pythonProject6\Scripts\python.exe tests\smoke_test_new_modules.py > d:\Liu\ai_platform_code\outputs\smoke.log 2>&1
echo EXITCODE=%ERRORLEVEL% >> d:\Liu\ai_platform_code\outputs\smoke.log
