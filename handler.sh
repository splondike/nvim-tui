#!/bin/sh
event=""
arg1=""
arg2=""
arg3=""
pos=0

while read line;do
    if [ $pos -eq 0 ];then
        event="$line"
    elif [ $pos -eq 1 ];then
        arg1="$line"
    elif [ $pos -eq 2 ];then
        arg2="$line"
    else
        arg3="$line"
    fi
    pos=$(echo "($pos + 1) % 4" | bc)
    if [ ! $pos -eq 0 ];then
        continue
    fi

    if [ "$event" = "url" ];then
        echo "setbuffer"
        echo "text 1"
        if [ x"$arg1" = x"" ];then
            arg1="/"
        fi
        if [ -d "$arg1" ];then
            find "$arg1" -maxdepth 1
        else
            cat $arg1
        fi
    elif [ "$event" = "primary" ];then
        echo "seturl"
        echo "$arg3" | awk '{$1="";print $0}'
    fi
    echo ".END."
done
