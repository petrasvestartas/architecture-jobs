@echo off
rem Light Swiss architecture job scan - runs every 30 min via the
rem "SwissArchJobScan" Task Scheduler task (07:00-21:00, also at logon,
rem catches up after boot). Scans the job boards + a rolling slice of
rem company career pages from this PC's home connection, then publishes
rem to GitHub Pages. Deliberately light so it is safe to repeat often;
rem the deep daily career-discovery pass runs in the cloud at 05:30.
cd /d "G:\My Drive\job\jobtracker"
set PYTHONIOENCODING=utf-8
python scraper.py --careers 40 --career-watch 250 >> scraper.log 2>&1
python publish_web.py >> scraper.log 2>&1
