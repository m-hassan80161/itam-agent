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
Source: "dist\ITAMAgent.exe"; DestDir: "{app}"; Flags: ignoreversion
; ملف الإعدادات المشفر
Source: "config.enc"; DestDir: "{commonappdata}\ITAM Agent"; Flags: onlyifdoesntexist; Permissions: users-modify

[Dirs]
Name: "{commonappdata}\ITAM Agent"; Permissions: users-modify

[Icons]
Name: "{group}\ITAM Agent"; Filename: "{app}\ITAMAgent.exe"

[Registry]
; تشغيل الوكيل بالخلفية عند تسجيل الدخول بعد إقلاع ويندوز
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ITAMAgent"; ValueData: """{app}\ITAMAgent.exe"""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\ITAMAgent.exe"; Description: "Launch ITAM Agent"; Flags: nowait postinstall skipifsilent