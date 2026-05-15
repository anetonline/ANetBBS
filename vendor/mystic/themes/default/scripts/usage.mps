// ==========================================================================
// USAGE.MPS : On the fly usage graph calculation for Mystic BBS v1.10+
//    Author : g00r00
//   Version : 1.1
//   License : Part of with Mystic BBS distribution / GPL repository
// --------------------------------------------------------------------------
//
// This MPL calculates a monthly, weekly and hourly usage graph based on the
// BBS history datafile.  Simply copy it to the scripts directory, compile it
// and execute it from your menu with the GX menu command (optional data
// 'usage').
//
// If the MPL program is executed without any optional data, it will allow
// the user to tab through the different graphs.  Additionally, the following
// optional command data options can be used:
//
//    MONTHLY - Display monthly graph and exit immediately
//    WEEKLY  - Display weekly graph and exit immediately
//    HOURLY  - Display hourly graph and exit immediately
//
// Example:
//
//     Menu Command: GX (Execute MPL Program)
//    Optional Data: usage weekly
//
// ==========================================================================

Uses CFG

Const
  fmRWDN = 66;

Type
  RecHistory = Record                        // From records.pas 1.10
    Date       : LongInt;
    Emails     : Word;
    Posts      : Word;
    Downloads  : Word;
    Uploads    : Word;
    DownloadKB : LongInt;
    UploadKB   : LongInt;
    Calls      : LongInt;
    NewUsers   : Word;
    Telnet     : Word;
    FTP        : Word;
    POP3       : Word;
    SMTP       : Word;
    NNTP       : Word;
    HTTP       : Word;
    Hourly     : Array[1..24] of Byte;
    Reserved   : Array[1..2] of Byte;
  End;

Var
  Days  : LongInt;
  Calls : LongInt;
  Month : Array[1..12] of Cardinal;
  Week  : Array[1..7] of Cardinal;
  Hour  : Array[1..24] of Cardinal;

Procedure DrawBar (XPos, bSize, Value: Byte);
Var
  Temp : Byte;
Begin
  For Temp := 1 to Value Do
    WriteXY (XPos, 18 - Temp, 1, strRep(#219, bSize));
End;

Procedure DisplayMonthly;
Var
  Count  : Byte;
  Count2 : Byte;
Begin
  WriteLn ('|CL|09|17 ' + #176 + ' |15Monthly Usage Graph ' + PadLT(strComma(Days) + '|07 days, |15' + strComma(Calls) + ' |07calls ', 63, ' ') + '|09' + #176 + ' |16');

  GotoXY  (6, 18);

  For Count := 1 to 12 Do
    Write ('|08' + #196 + #196 + #196 + '   ');

  WriteXY (6, 19, 14, 'Jan   Feb   Mar   Apr   May   Jun   Jul   Aug   Sep   Oct   Nov   Dec');
  WriteXY (2, 21, 08, strRep(#196, 78));

  For Count := 1 to 12 Do
    For Count2 := 15 DownTo 1 Do Begin
      GotoXY (Count * 6, 2 + Count2);
      Write  (#250 + #250 + #250);
    End;

  For Count := 1 to 12 Do
    DrawBar (6 * Count, 3, Month[Count]);
End;

Procedure DisplayWeekly;
Var
  Count  : Byte;
  Count2 : Byte;
Begin
  WriteLn ('|CL|09|17 ' + #176 + ' |15Weekly Usage Graph ' + PadLT(strComma(Days) + '|07 days, |15' + strComma(Calls) + ' |07calls ', 64, ' ') + '|09' + #176 + ' |16');

  For Count := 0 to 6 Do Begin
    GotoXY (4 + (Count * 11), 18);
    Write ('|08' + strRep(#196, 8));
  End;

  WriteXY ( 4, 19, 14, ' Sunday     Monday    Tuesday    Wednesday  Thursday    Friday    Saturday');
  WriteXY ( 2, 21, 08, strRep(#196, 78));

  For Count := 0 to 6 Do
    For Count2 := 15 DownTo 1 Do Begin
      GotoXY (4 + (Count * 11), 2 + Count2);
      Write  (strRep(#250, 8));
    End;

  For Count := 1 to 7 Do
    DrawBar (4 + ((Count - 1) * 11), 8, Week[Count]);
End;

Procedure DisplayHourly;
Var
  Count  : Integer;
  Count2 : Integer;
Begin
  WriteLn ('|CL|09|17 ' + #176 + ' |15Hourly Usage Graph ' + PadLT(strComma(Days) + '|07 days, |15' + strComma(Calls) + ' |07calls ', 64, ' ') + '|09' + #176 + ' |16');

  GotoXY (5, 18);

  For Count := 1 to 24 Do
    Write ('|08' + #196 + #196 + ' ');

  WriteXY ( 5, 19, 14, '12 01 02 03 04 05 06 07 08 09 10 11 12 01 02 03 04 05 06 07 08 09 10 11');
  WriteXY ( 5, 20, 09, 'AM');
  WriteXY (41, 20, 09, 'PM');
  WriteXY ( 2, 21, 08, strRep(#196, 78));

  For Count := 1 to 24 Do
    For Count2 := 15 DownTo 1 Do Begin
      GotoXY (5 + ((Count - 1) * 3), Count2 + 2);
      Write  ('|08' + #250 + #250);
    End;

  For Count := 1 to 24 Do
    DrawBar (5 + ((Count - 1) * 3), 2, Hour[Count]);
End;

Procedure CalculateHistory;
Var
  HistFile : LongInt;
  OneDay   : RecHistory;
  TempLong : Cardinal;
  TempReal : Real;
  Count    : LongInt;
  Highest  : Cardinal;
Begin
  ClassCreate(HistFile, 'File');

  If Not FileOpen(HistFile, CfgDataPath + 'history.dat', SizeOf(OneDay), 1, 66) Then Begin
    ClassFree(HistFile);

    Exit;
  End;

  If FileSize(HistFile) = 0 Then Begin
    ClassFree(HistFile);

    WriteLn ('|CRNo BBS history to calculate|CR|CR|PA');

    Halt;
  End;

  Days := FileSize(HistFile);

  WriteLn ('|16|CL|15Calculating usage for last ' + strComma(Days) + ' days...');

  While Not FileEOF(HistFile) Do Begin
    FileRead (Histfile, OneDay);

    Calls           := Calls + OneDay.Calls;
    TempLong        := Str2Int(Copy(DateStr(OneDay.Date, 1), 1, 2));
    Month[TempLong] := Month[TempLong] + OneDay.Calls;
    TempLong        := DayOfWeek(OneDay.Date) + 1;
    Week[TempLong]  := Week[TempLong] + OneDay.Calls;

    For Count := 1 to 24 Do
      Hour[Count] := Hour[Count] + OneDay.Hourly[Count];
  End;

  ClassFree(HistFile);

  Highest := 0;

  For Count := 1 to 12 Do
    If Month[Count] > Highest Then
      Highest := Month[Count];

  For Count := 1 to 12 Do
    If Month[Count] > 0 Then Begin
      TempReal     := (Month[Count] / Highest * 100);
      Month[Count] := TempReal / 7 + 1;
    End;

  Highest := 0;

  For Count := 1 to 7 Do
    If Week[Count] > Highest Then
      Highest := Week[Count];

  For Count := 1 to 7 Do
    If Week[Count] > 0 Then Begin
      TempReal    := (Week[Count] / Highest * 100);
      Week[Count] := TempReal / 7 + 1;
    End;

  Highest := 0;

  For Count := 1 to 24 Do
    If Hour[Count] > Highest Then
      Highest := Hour[Count];

  For Count := 1 to 24 Do
    If Hour[Count] > 0 Then Begin
      TempReal    := (Hour[Count] / Highest * 100);
      Hour[Count] := TempReal / 7 + 1;
    End;
End;

Var
  ShowMode : Byte;
Begin
  If Graphics = 0 Then Begin
    WriteLn ('|CRSorry, usage graphs require ANSI graphics|CR|CR|PA');
    Exit;
  End;

  CalculateHistory;

  If ParamCount > 0 Then Begin
    Case Upper(ParamStr(1)) of
      'MONTHLY' : DisplayMonthly;
      'WEEKLY'  : DisplayWeekly;
      'HOURLY'  : DisplayHourly;
    Else
      WriteLn ('USAGE.MPS: Invalid command line option.|PN');
    End;
  End Else Begin
    ShowMode := 1;

    Repeat
      Case ShowMode of
        1 : DisplayHourly;
        2 : DisplayWeekly;
        3 : DisplayMonthly;
      End;

      WriteXYPipe (22, 23, 7, 0, 'Press |08[|15TAB|08] |07for more or |08[|15ENTER|08] |07to Quit');

      Case OneKey(#09 + #13 + #27, False) of
        #09 : If ShowMode < 3 Then ShowMode := ShowMode + 1 Else ShowMode := 1;
        #13,
        #27 : Break;
      End;
    Until False;
  End;
End
