#!/bin/sh
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)" || exit 1
./start.sh --configure
result=$?
printf '\n按回车关闭。\n'
read -r unused
exit "$result"
