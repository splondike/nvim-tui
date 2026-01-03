#!/bin/sh
while read -r line;do
    action=$(echo "$line" | jq -r .action)
    if [ "$action" = "url" ];then
        path=$(echo "$line" | jq -r ".args[0]")
        if [ x"$path" = x"" ];then
            path="/"
        fi
        content=$({
            if [ -d "$path" ];then
                find "$path" -maxdepth 1
            else
                cat $path
            fi
        })
        jq -cn --arg arg "$content" '{"actions": [{"action": "setbuffer", "args": ["text false", $arg]}]}'
    elif [ "$action" = "primary" ];then
        url=$(echo "$line" | jq -r .selection_text)
        jq -cn --arg arg "$url" '{"actions": [{"action": "seturl", "args": [$arg]}]}'
    else
        echo '{"actions": []}'
    fi
done
