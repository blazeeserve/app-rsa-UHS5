#!/usr/bin/env bash
set -e
APP=${1:-pubostin-apk-bot}

heroku create "$APP" || true

heroku buildpacks:add --index 1 https://github.com/heroku/heroku-buildpack-apt   -a "$APP"
heroku buildpacks:add --index 2 https://github.com/heroku/heroku-buildpack-java  -a "$APP"
heroku buildpacks:add --index 3 heroku/python                                    -a "$APP"

heroku config:set MAX_APK_MB=300 -a "$APP"
echo "👉 Ab ye chalao:"
echo "   heroku config:set BOT_TOKEN=\"<token>\" -a $APP"
echo "   heroku ps:scale web=0 worker=1 -a $APP"
echo "   git push heroku main"
