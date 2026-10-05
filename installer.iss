[Setup]
AppName=IT Asset Inventory Agent
AppVersion=1.0
DefaultDirName={autopf}\ITAM Agent
DefaultGroupName=ITAM Agent
OutputBaseFilename=ITAM_Agent_Setup
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin
OutputDir=Output

[Files]
; الملف التنفيذي الناتج من PyInstaller
Source: "dist\main.exe"; DestDir: "{app}"; Flags: ignoreversion
; ملف الإعدادات المشفر
Source: "config.enc"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\ITAM Agent"; Filename: "{app}\main.exe"

[Registry]
; جعل البرنامج يعمّل تلقائياً مع إقلاع نظام ويندوز
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ITAMAgent"; ValueData: """{app}\main.exe"""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\main.exe"; Description: "Launch ITAM Agent"; Flags: nowait postinstall skipifsilent