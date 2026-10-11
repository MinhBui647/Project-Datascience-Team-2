@echo off
title Sync Local to Cloud (Hugging Face)
echo ======================================================================
echo   Pushing local raw_data to Hugging Face Dataset Hub
echo ======================================================================
python sync_local_to_cloud.py
echo.
pause

