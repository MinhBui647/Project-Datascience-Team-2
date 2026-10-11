@echo off
title Sync Cloud to Local (Hugging Face)
echo ======================================================================
echo   Pulling latest raw_data from Hugging Face Dataset Hub
echo ======================================================================
python sync_cloud_to_local.py
echo.
pause

