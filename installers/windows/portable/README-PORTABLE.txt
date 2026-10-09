Ninaivu Lite - portable (no install)
====================================

1. Before extracting: right-click the downloaded zip, choose Properties, tick
   "Unblock" (bottom of the General tab) and press OK. Windows then stops
   asking "Do you want to open this file?" about every part of it.
2. Right-click the zip, choose Extract All, and pick a folder that will stay
   put - for example C:\Ninaivu-Lite, or a pendrive. A path with Tamil
   letters or spaces is fine.
3. Open the "Ninaivu Lite" folder and double-click "Ninaivu Lite.exe" (the
   Control Panel). Press Start, then "Open the family app".

Everything stays in the "Ninaivu Lite" folder, and its top holds only
"Ninaivu Lite.exe" and its log, "Ninaivu Lite.log". The program is in the
"app" folder (where this file is), and the family's settings, people, index
and previews are in the "data" folder that appears beside it the first time
it starts. The photographs themselves are only
read, never moved. Nothing is written to the Start menu, the registry or
AppData, unless you tick "Start Ninaivu Lite when I sign in" in the Control
Panel (that adds one small file to your Startup folder; untick it before
moving or deleting this folder).

Moving it: close the Control Panel after pressing Stop, then move or copy
the whole folder. Upgrading (Ninaivu Lite never checks for a newer version;
download one when you choose to): first, in the old folder's Control Panel,
press Stop, untick "Start Ninaivu Lite when I sign in" and close the Control
Panel. Then extract the new version to a new folder, copy the old "data"
folder into its "Ninaivu Lite" folder (beside Ninaivu Lite.exe), and tick the box again in the new Control Panel (otherwise the
old copy keeps starting, and changes go into its data). Never extract a new
version over a copy that is running. The first start of the new version keeps
a copy of the old index in data\backups before it brings the index forward. Removing it: press Stop, untick
"Start Ninaivu Lite when I sign in", and delete the folder.

Setting up from a phone asks for a setup code: the Control Panel shows it,
and it is in "Ninaivu Lite.log" beside Ninaivu Lite.exe ("Open the log").

Forgotten password: press Stop, then in a Command Prompt in the "Ninaivu Lite"
folder (where Ninaivu Lite.exe is) run
  app\Python\python.exe -m ninaivu_lite --data "%CD%\data" --reset-password NAME
A daily backup is kept in data\backups (the last seven). To put one back,
press Stop and run the same line with --restore <zip> in place of
--reset-password NAME.

Not signed: Ninaivu Lite is free and is not code-signed, so if the zip was not
unblocked (step 1), Windows may say "Windows protected your PC" when
Ninaivu Lite.exe is first opened: press "More info", then "Run anyway". The
Python inside (app\Python\pythonw.exe) is signed by the Python Software
Foundation.


நினைவு லைட் - கையடக்கப் பதிப்பு (நிறுவல் தேவையில்லை)
==================================================

1. விரிப்பதற்கு முன்: பதிவிறக்கிய zip மீது வலது சொடுக்கு -> Properties ->
   General தாவலின் கீழே "Unblock" ஐத் தேர்ந்தெடுத்து OK அழுத்தவும். அப்போது
   ஒவ்வொரு கோப்பையும் திறக்கலாமா என்று Windows கேட்காது.
2. zip மீது வலது சொடுக்கு -> Extract All. நிலையாக இருக்கும் ஒரு கோப்புறையைத்
   தேர்ந்தெடுக்கவும் (எ.கா. C:\Ninaivu-Lite, அல்லது ஒரு பென்டிரைவ்). தமிழ்
   எழுத்துகளோ இடைவெளிகளோ உள்ள பாதையும் சரிதான்.
3. "Ninaivu Lite" கோப்புறையைத் திறந்து "Ninaivu Lite.exe" (கட்டுப்பாட்டுப்
   பலகம்) ஐ இருமுறை சொடுக்கவும். Start அழுத்தி, பின் "Open the family app"
   அழுத்தவும்.

எல்லாமே "Ninaivu Lite" கோப்புறைக்குள்ளேயே இருக்கும்; அதன் மேல்மட்டத்தில்
"Ninaivu Lite.exe" உம் அதன் பதிவுக் கோப்பு "Ninaivu Lite.log" உம் மட்டுமே.
நிரல் "app" கோப்புறையில் (இந்தக் கோப்பு உள்ள இடம்); குடும்பத்தின் அமைப்புகள்,
நபர்கள், அட்டவணை, முன்னோட்டங்கள் முதல் தொடக்கத்தில் அதன் அருகே உருவாகும்
"data" கோப்புறையில். புகைப்படங்கள் படிக்க மட்டுமே படும்;
நகர்த்தப்படாது. Start மெனு, registry, AppData எதிலும் எதுவும் எழுதப்படாது;
Control Panel இல் "Start Ninaivu Lite when I sign in" ஐத் தேர்ந்தெடுத்தால் மட்டும்
Startup கோப்புறையில் ஒரு சிறு கோப்பு சேரும் - இந்தக் கோப்புறையை நகர்த்தும் அல்லது
நீக்கும் முன் அந்தத் தேர்வை நீக்கவும்.

நகர்த்த: Stop அழுத்தி Control Panel ஐ மூடி, முழுக் கோப்புறையையும் நகர்த்தவும்
அல்லது நகலெடுக்கவும். புதுப்பிக்க (நினைவு லைட் புதிய பதிப்பைத் தானாகத் தேடுவதில்லை;
நீங்கள் விரும்பும்போது பதிவிறக்குங்கள்): முதலில் பழைய கோப்புறையின் Control Panel இல்
Stop அழுத்தி, "Start Ninaivu Lite when I sign in" தேர்வை நீக்கி, Control Panel ஐ
மூடவும்; பின் புதிய
பதிப்பை ஒரு புதிய கோப்புறையில் விரித்து, பழைய "data" கோப்புறையை அதன் "Ninaivu Lite"
கோப்புறைக்குள் (Ninaivu Lite.exe அருகே) நகலெடுத்து, புதிய Control Panel இல் அந்தத் தேர்வை மீண்டும் இடவும் (இல்லையெனில்
பழைய நகலே தொடங்கும், மாற்றங்கள் அதன் தரவில் சேரும்). இயங்கிக்கொண்டிருக்கும்
நகலின் மேல் புதிய பதிப்பை ஒருபோதும் விரிக்க வேண்டாம். புதிய பதிப்பின் முதல்
தொடக்கம், அட்டவணையை மாற்றும் முன் அதன் பழைய நகலை data\backups இல் வைக்கும். நீக்க: Stop அழுத்தி,
"Start Ninaivu Lite when I sign in" தேர்வை நீக்கி, கோப்புறையை அழிக்கவும்.

கைப்பேசியிலிருந்து அமைத்தால் ஓர் அமைப்புக் குறியீடு கேட்கப்படும்: Control Panel
அதைக் காட்டும்; Ninaivu Lite.exe அருகிலுள்ள "Ninaivu Lite.log" இலும் இருக்கும் ("Open the log").

கடவுச்சொல் மறந்துவிட்டதா: Stop அழுத்தி, "Ninaivu Lite" கோப்புறையில் (Ninaivu Lite.exe
உள்ள இடம்) ஒரு Command Prompt-இல்
  app\Python\python.exe -m ninaivu_lite --data "%CD%\data" --reset-password NAME
இயக்கவும் (NAME என்பது பயனர்பெயர்). தினசரி காப்புப் பிரதி data\backups இல்
இருக்கும் (கடைசி ஏழு). ஒன்றைத் திரும்ப வைக்க, Stop அழுத்தி, அதே வரியில்
--reset-password NAME க்குப் பதிலாக --restore <zip> கொடுத்து இயக்கவும்.

கையொப்பம் இல்லை: நினைவு லைட் இலவசம்; அதற்குக் குறியீட்டுக் கையொப்பம் (code
signing) இல்லை; zip ஐ (படி 1) Unblock செய்யாவிட்டால், Ninaivu Lite.exe ஐ முதலில்
திறக்கும்போது Windows "Windows protected your PC" எனலாம்: "More info", பின்
"Run anyway" அழுத்தவும். உள்ளே உள்ள Python (app\Python\pythonw.exe) Python
Software Foundation-ஆல் கையொப்பமிடப்பட்டது.
