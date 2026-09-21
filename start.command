#!/bin/sh
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)" || exit 1
./start.sh "$@"
result=$?
if [ "$result" -ne 0 ]; then
    printf '\n启动未完成，请查看上面的原因；按回车关闭。\n'
    read -r unused
fi
exit "$result"
