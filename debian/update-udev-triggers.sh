#!/bin/bash
set -e

srcdir="${GBP_SOURCES_DIR:-.}"
debpath="$(dirname "$0")"
autosuspend_file="$srcdir/data/autosuspend.hwdb"
commands_lines=()

while IFS= read -r line; do
    if [[ $line =~ ^usb:v([A-Fa-f0-9]{4})p([A-Fa-f0-9]{4}) ]]; then
        vendor="$(echo "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]')"
        product="$(echo "${BASH_REMATCH[2]}" | tr '[:upper:]' '[:lower:]')"
        commands_lines+=("\tudevadm trigger --action=add --attr-match=idVendor=$vendor --attr-match=idProduct=$product")
    fi
done < "$autosuspend_file"

export UDEVADM_TRIGGERS=$( IFS=$'\n'; echo -e "${commands_lines[*]}" )

for i in $debpath/libfprint-*.post*.in; do
    out="${i%.in}"
    envsubst < "$i" > "$out"

    if [ -n "$GBP_BRANCH" ]; then
        if ! git diff-index --quiet HEAD -- "$out"; then
            git add "$out"
            dch "${out#$srcdir}: Devices triggers updated"
            git add debian/changelog
            debcommit
        fi
    fi
done
