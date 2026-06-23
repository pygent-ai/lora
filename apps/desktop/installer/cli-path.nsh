!include "LogicLib.nsh"

!ifdef BUILD_UNINSTALLER

!macro customUnInstall
  Push "$INSTDIR\resources\backend\lora-api"
  Call un.RemoveLoraCliFromUserPath
!macroend

Function un.RemoveLoraCliFromUserPath
  Exch $0
  Push $1
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7

  ReadRegStr $1 HKCU "Environment" "Path"
  StrCpy $2 ""
  StrCpy $3 "$1"

nextSegment:
  ${If} $3 == ""
    Goto writePath
  ${EndIf}

  StrCpy $5 0

findSeparator:
  StrCpy $6 "$3" 1 $5
  ${If} $6 == ""
    Goto segmentFound
  ${EndIf}
  ${If} $6 == ";"
    Goto segmentFound
  ${EndIf}
  IntOp $5 $5 + 1
  Goto findSeparator

segmentFound:
  StrCpy $7 "$3" $5
  ${If} $6 == ";"
    IntOp $5 $5 + 1
    StrCpy $3 "$3" "" $5
  ${Else}
    StrCpy $3 ""
  ${EndIf}

  ${If} $7 == ""
    Goto nextSegment
  ${EndIf}
  ${If} $7 == $0
    Goto nextSegment
  ${EndIf}

  ${If} $2 == ""
    StrCpy $2 "$7"
  ${Else}
    StrCpy $2 "$2;$7"
  ${EndIf}

  Goto nextSegment

writePath:
  ${If} $1 != $2
    WriteRegExpandStr HKCU "Environment" "Path" "$2"
    SendMessage 0xFFFF 0x001A 0 "STR:Environment" /TIMEOUT=5000
  ${EndIf}

  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

!else

!macro customInstall
  Push "$INSTDIR\resources\backend\lora-api"
  Call AddLoraCliToUserPath
!macroend

Function AddLoraCliToUserPath
  Exch $0
  Push $1
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7

  IfFileExists "$0\lora.exe" 0 done

  ReadRegStr $1 HKCU "Environment" "Path"
  ${If} $1 == ""
    WriteRegExpandStr HKCU "Environment" "Path" "$0"
    SendMessage 0xFFFF 0x001A 0 "STR:Environment" /TIMEOUT=5000
  ${Else}
    StrCpy $2 "0"
    StrCpy $3 "$1"
    Goto addNextSegment
  ${EndIf}

  Goto done

addNextSegment:
  ${If} $3 == ""
    Goto addMaybeAppend
  ${EndIf}

  StrCpy $5 0

addFindSeparator:
  StrCpy $6 "$3" 1 $5
  ${If} $6 == ""
    Goto addSegmentFound
  ${EndIf}
  ${If} $6 == ";"
    Goto addSegmentFound
  ${EndIf}
  IntOp $5 $5 + 1
  Goto addFindSeparator

addSegmentFound:
  StrCpy $7 "$3" $5
  ${If} $6 == ";"
    IntOp $5 $5 + 1
    StrCpy $3 "$3" "" $5
  ${Else}
    StrCpy $3 ""
  ${EndIf}

  ${If} $7 == $0
    StrCpy $2 "1"
    Goto addMaybeAppend
  ${EndIf}

  Goto addNextSegment

addMaybeAppend:
  ${If} $2 == "0"
    WriteRegExpandStr HKCU "Environment" "Path" "$1;$0"
    SendMessage 0xFFFF 0x001A 0 "STR:Environment" /TIMEOUT=5000
  ${EndIf}

done:
  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

!endif
