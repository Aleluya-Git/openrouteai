#!/usr/bin/env bash
# Compila el núcleo nativo. Requiere clang (y lld-link para la DLL de Windows).
set -e
cd "$(dirname "$0")"
clang -O3 -shared -fPIC -fvisibility=hidden -o ora_search_linux_x86_64.so ora_search.c
clang --target=x86_64-pc-windows-msvc -O3 -ffreestanding -fno-builtin -fno-stack-protector \
      -mno-stack-arg-probe -DORA_FREESTANDING -c ora_search.c -o ora_win.obj
lld-link /dll /noentry /nodefaultlib /machine:x64 /out:ora_search_win_amd64.dll ora_win.obj
rm -f ora_win.obj ora_search_win_amd64.lib
echo "OK: ora_search_linux_x86_64.so ora_search_win_amd64.dll"
