@echo off
title Daily EPL Raw Crawler
echo ======================================================================
echo   Running Daily Premier League Raw Crawl Pipeline (2026/27)
echo ======================================================================
python crawler_pipeline\daily_raw_crawler.py
echo.
echo Daily raw crawl finished.
pause

