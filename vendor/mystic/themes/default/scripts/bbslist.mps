// -------------------------------------------------------------------------
// BBSLIST.MPS : BBS list creator for Mystic BBS software v1.07+
// -------------------------------------------------------------------------
// This program will export the BBS list data to a text file, and allow the
// user to download the created text file.
//
// The command line option specifies the base name of the BBS list to go along
// with the same parameters of the BBS list menu commands:
//
// bbslist [bbslist name]  IE "bbslist bbslist"
//
// -------------------------------------------------------------------------

Uses
  CFG,
  USER

Var
  ListFile     : File;
  ListName     : String
  OutFile      : File;
  OutName      : String
  bbs_cType    : Byte
  bbs_Phone    : String
  bbs_Telnet   : String
  bbs_Name     : String
  bbs_Location : String
  bbs_Sysop    : String
  bbs_Baud     : String
  bbs_Software : String
  bbs_Deleted  : Boolean
  bbs_AddedBy  : String
  bbs_Verified : LongInt
  bbs_Extra1   : LongInt
  bbs_Extra2   : Integer
  Total        : Integer
  Temp         : String

Begin
  If ParamCount <> 1 Then Begin
    WriteLn ('Invalid command line option.');
    WriteLn ('');
    WriteLn ('Usage: BBSLIST [bbs list id]');
    WriteLn ('|CR|PA');
    Halt
  End

  GetThisUser

  ListName := CfgDataPath + ParamStr(1) + '.bbi';
  OutName  := CfgSysPath  + 'temp' + Int2Str(NodeNum) + PathChar + 'bbslist.txt';

  If Not FileExist(ListName) Then Begin
    WriteLn ('|CR|12There are no entries in the BBS list.');
    Halt;
  End;

  If Not InputYN('|CR|12Download the BBS list? ') Then
    Halt

  Write ('|CR|14Creating BBS list ... ')

  fAssign (ListFile, ListName, 66);
  fReset  (ListFile);

  If IoResult <> 0 Then Begin
    WriteLn('Unable to find BBS list data');
    Halt;
  End;

  fAssign  (OutFile, OutName, 66);
  fReWrite (OutFile);

  fWriteLn (OutFile, '')
  fWriteLn (OutFile, '.-------------------------------------------.')
  fWriteLn (OutFile, '| BBS listing created on ' + DateStr(DateTime, UserDateType) + ' at ' + TimeStr(DateTime, True) + ' |')
  fWriteLn (OutFile, '`-------------------------------------------''')
  fWriteLn (OutFile, '')

  Total := 0;

  While Not fEof(ListFile) Do Begin
    fRead (ListFile, bbs_cType,     1)
    fRead (ListFile, bbs_Phone,    16)
    fRead (ListFile, bbs_Telnet,   41)
    fRead (ListFile, bbs_Name,     31)
    fRead (ListFile, bbs_Location, 26)
    fRead (ListFile, bbs_Sysop,    31)
    fRead (ListFile, bbs_Baud,      7)
    fRead (ListFile, bbs_Software, 11)
    fRead (ListFile, bbs_Deleted,   1)
    fRead (ListFile, bbs_AddedBy,  31)
    fRead (ListFile, bbs_Verified,  4)
    fRead (ListFile, bbs_Extra1,    4)
    fRead (ListFile, bbs_Extra2,    2)

    If Not bbs_Deleted Then Begin
      Total := Total + 1

      fWriteLn (OutFile, '      BBS Name: ' + bbs_Name)

      If bbs_cType = 0 Then Begin
        fWriteLn (OutFile, ' Accessible By: Dialup')
        fWriteLn (OutFile, '  Phone Number: ' + bbs_Phone)
        fWriteLn (OutFile, ' Max Baud Rate: ' + bbs_Baud)
      End

      If bbs_cType = 1 Then Begin
        fWriteLn (OutFile, ' Accessible By: Telnet')
        fWriteLn (OutFile, '        Telnet: ' + bbs_Telnet)
      End

      If bbs_cType = 2 Then Begin
        fWriteLn (OutFile, ' Accessible By: Dialup & Telnet')
        fWriteLn (OutFile, '  Phone Number: ' + bbs_Phone)
        fWriteLn (OutFile, ' Max Baud Rate: ' + bbs_Baud)
        fWriteLn (OutFile, '        Telnet: ' + bbs_Telnet)
      End

      fWriteLn (OutFile, '    Sysop Name: ' + bbs_Sysop)
      fWriteLn (OutFile, '      Location: ' + bbs_Location)
      fWriteLn (OutFile, '  BBS Software: ' + bbs_Software)
      fWriteLn (OutFile, ' Last Verified: ' + DateStr(bbs_Verified, UserDateType))

      fWriteLn (OutFile, '')
      fWriteLn (OutFile, '----------------------------------------------------------')
      fWriteLn (OutFile, '')
    End
  End;

  fWriteLn (OutFile, 'Total BBSes listed: ' + Int2Str(Total))

  fClose (ListFile)
  fClose (OutFile)

  WriteLn ('Done.')

  If Local Then Begin
    If InputYN ('|CR|12Local mode: Save list to file? |11') Then Begin
      Write ('|CR|03Enter full path and filename for BBS list|CR|09:')
      Temp := Input (60, 60, 12, CfgSysPath + 'bbslist.txt')
      If Temp <> '' Then Begin
        Write ('|CR|14Saving: |15' + Temp + '|14: ')
        If FileCopy(OutName, Temp) Then
          WriteLn ('OK')
        Else
          WriteLn ('ERROR')
      End
    End
  End Else
    MenuCmd ('F3', OutName)

  FileErase (OutName)
End;
