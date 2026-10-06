Ninaivu Lite - portable (no install)
====================================

1. Before extracting: right-click the downloaded zip, choose Properties, tick
   "Unblock" (bottom of the General tab) and press OK. Windows then stops
   asking "Do you want to open this file?" about every part of it.
2. Right-click the zip, choose Extract All, and pick a folder that will stay
   put - for example C:\Ninaivu-Lite, or a pendrive. A path with Tamil
   letters or spaces is fine.
3. Open the folder and double-click "Ninaivu Lite Control Panel.vbs".
   Press Start, then "Open the family app".

Everything stays in this folder. The program is beside this file, and the
family's settings, people, index and previews are in the "data" folder that
appears here the first time it starts. The photographs themselves are only
read, never moved. Nothing is written to the Start menu, the registry or
AppData, unless you tick "Start Ninaivu Lite when I sign in" in the Control
Panel (that adds one small file to your Startup folder; untick it before
moving or deleting this folder).

Moving it: close the Control Panel after pressing Stop, then move or copy
the whole folder. Upgrading: extract the new version to a new folder, then
copy the old "data" folder into it. Removing it: press Stop, untick
"Start Ninaivu Lite when I sign in", and delete the folder.

Setting up from a phone asks for a setup code: the Control Panel shows it,
and it is in data\logs\ninaivu-lite.log ("Open the log").

Forgotten password: press Stop, then in a Command Prompt in this folder run
  Python\python.exe -m ninaivu_lite --data "%CD%\data" --reset-password NAME
A daily backup is kept in data\backups (the last seven). To put one back,
press Stop and run the same line with --restore <zip> in place of
--reset-password NAME.

Not signed: Ninaivu Lite is free and is not code-signed. The Python inside
(Python\pythonw.exe) is signed by the Python Software Foundation.


நினைவு லைட் - கையடக்கப் பதிப்பு (நிறுவல் தேவையில்லை)
==================================================

1. விரிப்பதற்கு முன்: பதிவிறக்கிய zip மீது வலது சொடுக்கு -> Properties ->
   General தாவலின் கீழே "Unblock" ஐத் தேர்ந்தெடுத்து OK அழுத்தவும். அப்போது
   ஒவ்வொரு கோப்பையும் திறக்கலாமா என்று Windows கேட்காது.
2. zip மீது வலது சொடுக்கு -> Extract All. நிலையாக இருக்கும் ஒரு கோப்புறையைத்
   தேர்ந்தெடுக்கவும் (எ.கா. C:\Ninaivu-Lite, அல்லது ஒரு பென்டிரைவ்). தமிழ்
   எழுத்துகளோ இடைவெளிகளோ உள்ள பாதையும் சரிதான்.
3. அந்தக் கோப்புறையில் "Ninaivu Lite Control Panel.vbs" ஐ இருமுறை சொடுக்கவும்.
   Start அழுத்தி, பின் "Open the family app" அழுத்தவும்.

எல்லாமே இந்தக் கோப்புறைக்குள்ளேயே இருக்கும்: நிரல் இந்தக் கோப்பின் அருகிலும்,
குடும்பத்தின் அமைப்புகள், நபர்கள், அட்டவணை, முன்னோட்டங்கள் முதல் தொடக்கத்தில்
இங்கே உருவாகும் "data" கோப்புறையிலும். புகைப்படங்கள் படிக்க மட்டுமே படும்;
நகர்த்தப்படாது. Start மெனு, registry, AppData எதிலும் எதுவும் எழுதப்படாது;
Control Panel இல் "Start Ninaivu Lite when I sign in" ஐத் தேர்ந்தெடுத்தால் மட்டும்
Startup கோப்புறையில் ஒரு சிறு கோப்பு சேரும் - இந்தக் கோப்புறையை நகர்த்தும் அல்லது
நீக்கும் முன் அந்தத் தேர்வை நீக்கவும்.

நகர்த்த: Stop அழுத்தி Control Panel ஐ மூடி, முழுக் கோப்புறையையும் நகர்த்தவும்
அல்லது நகலெடுக்கவும். புதுப்பிக்க: புதிய பதிப்பை ஒரு புதிய கோப்புறையில்
விரித்து, பழைய "data" கோப்புறையை அதற்குள் நகலெடுக்கவும். நீக்க: Stop அழுத்தி,
"Start Ninaivu Lite when I sign in" தேர்வை நீக்கி, கோப்புறையை அழிக்கவும்.

கைப்பேசியிலிருந்து அமைத்தால் ஓர் அமைப்புக் குறியீடு கேட்கப்படும்: Control Panel
அதைக் காட்டும்; data\logs\ninaivu-lite.log இலும் இருக்கும் ("Open the log").

கடவுச்சொல் மறந்துவிட்டதா: Stop அழுத்தி, இந்தக் கோப்புறையில் ஒரு Command Prompt-இல்
  Python\python.exe -m ninaivu_lite --data "%CD%\data" --reset-password NAME
இயக்கவும் (NAME என்பது பயனர்பெயர்). தினசரி காப்புப் பிரதி data\backups இல்
இருக்கும் (கடைசி ஏழு). ஒன்றைத் திரும்ப வைக்க, Stop அழுத்தி, அதே வரியில்
--reset-password NAME க்குப் பதிலாக --restore <zip> கொடுத்து இயக்கவும்.

கையொப்பம் இல்லை: நினைவு லைட் இலவசம்; அதற்குக் குறியீட்டுக் கையொப்பம் (code
signing) இல்லை. உள்ளே உள்ள Python (Python\pythonw.exe) Python Software
Foundation-ஆல் கையொப்பமிடப்பட்டது.
