// ==========================================================================
// BLACKJACK.MPS
//
// This is a simple BlackJack game that I wrote to test out MPL features
// about a year or two ago.  I decided to port it to the later MPL version
// for the same purposes.
//
// Changelog:
//   - Added an improved AI for the dealer.  He's a lot less predictable and
//     makes more logical decisions now.
//   - When the player busts, the dealers hidden card is now shown.  This is
//     just for people curious if they would have won by standing.
//   - Fixed a few display bugs
//   - Now saves your money between sessions
//   - Added Top 10 list
//   - Added command line option RESET to reset scores
//   - Added command line option TOP10 to show top 10 and exit
//   - No longer allows negative numbers to be a Wager.
// ==========================================================================

Uses
  User;

Const
  Version     = '1.4';
  CashStart   = 1000;
  CardJack    = 11;
  CardQueen   = 12;
  CardKing    = 13;
  CardAce     = 14;
  SuitClub    = 1;
  SuitSpade   = 2;
  SuitHeart   = 3;
  SuitDiamond = 4;

Type
  PlayerRec = Record
    UserID : LongInt;
    Name   : String[30];
    Cash   : LongInt;
    LastOn : LongInt;
  End;

Type
  TopTenRec = Record
    User : String[35];
    Cash : LongInt;
    Date : LongInt;
  End;

Type
  CardRec = Record
    Suit : Byte;
    Card : Byte;
  End;

Var
  DataPath      : String;
  Deck          : Array[1..52] of CardRec;
  Player        : PlayerRec;
  PlayerNumber  : LongInt = -1;
  Wager         : LongInt;
  Player_Score  : Byte;
  Player_Cards  : Byte;
  Dealer_Score  : Byte;
  Dealer_Hidden : Byte;
  Dealer_Cards  : Byte;
  Dealer_Aces   : Byte;

Procedure LoadPlayer;
Var
  F : File;
  T : PlayerRec;
Begin
  GetThisUser;

  PlayerNumber  := -1;

  Player.UserID := UserIndex;
  Player.Cash   := CashStart;

  fAssign (F, DataPath + 'blackjack.ply', 66);
  fReset  (F);

  If IoResult <> 0 Then fReWrite(F);

  While Not fEof(F) Do Begin
    fReadRec (F, T);

    If T.UserID = UserIndex Then Begin
      Player       := T;
      PlayerNumber := fPos(F) / SizeOf(Player);
      Break;
    End;
  End;

  fClose (F);

  Player.LastOn := DateTime;
  Player.Name   := UserAlias;
End;

Procedure SavePlayer;
Var
  F : File;
Begin
  fAssign (F, DataPath + 'blackjack.ply', 66);
  fReset  (F);

  If PlayerNumber <> -1 Then
    fSeek (F, SizeOf(Player) * (PlayerNumber - 1));
  Else
    fSeek (F, fSize(F));

  fWriteRec (F, Player);
  fClose    (F);
End;

Procedure ExecuteTopTen;
Var
  TopList   : Array[1..10] of TopTenRec;
  Count1    : Byte;
  Count2    : Byte;
  Count3    : Byte;
  F         : File;
  OnePerson : PlayerRec;
Begin
  Write ('|16|CL|10Sorting top scores...');

  For Count1 := 1 to 10 Do Begin
    TopList[Count1].User := 'None';
    TopList[Count1].Cash := 0;
    TopList[Count1].Date := 0;
  End;

  fAssign (F, DataPath + 'blackjack.ply', 66);
  fReset  (F);

  If IoResult = 0 Then
    While Not fEof(F) Do Begin
      fReadRec (F, OnePerson);

      For Count2 := 1 to 10 Do
        If TopList[Count2].Cash <= OnePerson.Cash Then Begin
          For Count3 := 10 DownTo Count2 + 1 Do
            TopList[Count3] := TopList[Count3 - 1]

          TopList[Count2].Cash := OnePerson.Cash;
          TopList[Count2].User := OnePerson.Name;
          TopList[Count2].Date := OnePerson.LastOn;

          Break;
        End;
    End;

  ClrScr;

  GotoXY (21, 3);
  Write  ('|07Mystic BlackJack - Top 10 Money Holders');

  GotoXY (5, 6);
  Write  ('##  User                              Date                      Cash');

  GotoXY (5, 7);
  Write  ('|02' + strRep(#196, 68) + '|10');

  For Count1 := 1 to 10 Do Begin
    GotoXY (5, 7 + Count1);
    Write  (PadLT(Int2Str(Count1), 2, ' '));

    GotoXY (9, 7 + Count1);
    Write  (TopList[Count1].User);

    GotoXY (42, 7 + Count1);
    Write  (DateStr(TopList[Count1].Date, 1));

    GotoXY (53, 7 + Count1);
    Write  (PadLT(strComma(TopList[Count1].Cash), 20, ' '));
  End;

  GotoXY (5, 18);
  Write  ('|02' + strRep(#196, 68));

  GotoXY (26, 20);
  Write  ('|02Press |08[|15ENTER|08] |02to continue|PN');
End;

Procedure DeckCreate;
Var
  Suits,
  Numbers,
  Index    : Byte;
Begin
  Index := 1;

  For Suits := 1 to 4 Do
    For Numbers := 2 to CardAce Do Begin
      Deck[Index].Suit := Suits;
      Deck[Index].Card := Numbers;
      Index            := Index + 1;
    End;
End;

Procedure DeckShuffle;
Var
  OneCard   : CardRec;
  Shuffle,
  CardNum1,
  CardNum2  : Byte;
Begin
  For Shuffle := 1 to 200 Do Begin
    CardNum1       := Random(51) + 1;
    CardNum2       := Random(51) + 1;
    OneCard        := Deck[CardNum1];
    Deck[CardNum1] := Deck[CardNum2];
    Deck[CardNum2] := OneCard;
  End;
End;

Function GetCardNumber (Num: Byte) : String;
Var
  Res,
  Color : String[3];
Begin
  Case Deck[Num].Card of
    1..10     : Res := PadLT(Int2Str(Deck[Num].Card), 2, ' ');
    CardJack  : Res := ' J';
    CardQueen : Res := ' Q';
    CardKing  : Res := ' K';
    CardAce   : Res := ' A';
  End;

  Case Deck[Num].Suit of
    SuitClub    : GetCardNumber := '|08' + Res + #05;
    SuitSpade   : GetCardNumber := '|08' + Res + #06;
    SuitHeart   : GetCardNumber := '|04' + Res + #03;
    SuitDiamond : GetCardNumber := '|04' + Res + #04;
  End
End

Procedure DrawCard (X, Y, Showing, Num: Byte);
Var
  Str : String;
Begin
  If Y = 1 Then Y := 17 Else Y := 10;

  X   := (X - 1) * 9 + 5;
  Str := GetCardNumber(Num);

  Case Showing of
    1 : Begin
          GotoXY (X, Y);
          Write  ('|23' + Str + '   ');
          GotoXY (X, Y + 1);
          Write  ('      ');
          GotoXY (X, Y + 2);
          Write  ('   ' + Str + '|16');
        End;
    2 : Begin
          GotoXY (X, Y);
          Write  ('|07|20° °° °');
          GotoXY (X, Y + 1);
          Write  ('° °° °');
          GotoXY (X, Y + 2);
          Write  ('° °° °|16');
        End;
  Else
    GotoXY (X, Y);
    Write  ('|00|16      ');
    GotoXY (X, Y + 1);
    Write  ('      ');
    GotoXY (X, Y + 2);
    Write  ('      |07');
  End;
End;

Procedure Print (Str1, Str2: String);
Begin
  GotoXY (54, 13);
  Write  (strRep(' ', 23));
  GotoXY (54, 13);
  Write  (Str1);
  GotoXY (54, 14);
  Write  (strRep(' ', 23));
  GotoXY (54, 14);
  Write  (Str2);
End

Procedure GetNewCard (Dealer: Boolean);
Var
  Count,
  Value,
  Aces   : Byte;
Begin
  Aces        := 0;
  Dealer_Aces := 0;

  If Dealer Then Begin
    Dealer_Score := 0;
    Dealer_Cards := Dealer_Cards + 1;

    DrawCard (Dealer_Cards, 2, 1, Dealer_Cards + 5);

    For Count := 1 to Dealer_Cards Do Begin
      Value := Deck[Count + 5].Card;
      If Value = CardAce Then Begin
        Value       := 11;
        Dealer_Aces := Dealer_Aces + 1;
      End Else
      If Value > 10 Then
        Value := 10;

      Dealer_Score := Dealer_Score + Value;
    End;

    If Dealer_Score > 21 And Dealer_Aces > 0 Then Begin
      Repeat
        Dealer_Score := Dealer_Score - 10;
        Dealer_Aces  := Dealer_Aces - 1;
      Until Dealer_Score < 22 or Dealer_Aces = 0;

      If Deck[6].Card = CardAce And Dealer_Aces = 0 Then
        Dealer_Hidden := 1;
    End;

  End Else Begin

    Player_Score := 0;
    Player_Cards := Player_Cards + 1;

    DrawCard (Player_Cards, 1, 1, Player_Cards);

    For Count := 1 to Player_Cards Do Begin
      Value := Deck[Count].Card;
      If Value = CardAce Then Begin
        Value := 11;
        Aces  := Aces + 1;
      End Else
      If Value > 10 Then
        Value := 10;

      Player_Score := Player_Score + Value;
    End;

    If Player_Score > 21 Then
      While Player_Score > 21 And Aces > 0 Do Begin
        Player_Score := Player_Score - 10;
        Aces         := Aces - 1;
      End;
  End;
End;

Procedure DrawCash
Begin
  GotoXY (64, 19);
  Write  ('|15|17' + PadRT(strComma(Player.Cash), 10, ' ') + '|16');
End;

Procedure UpdateScores;
Begin
  GotoXY (65, 10);
  Write  ('|15' + Int2Str(Dealer_Score - Dealer_Hidden));
  GotoXY (65, 17);
  Write  (Int2Str(Player_Score));
End

Procedure Initialize;

  Procedure EraseInput;
  Begin
    GotoXY (64, 20);
    Write  ('|17          |16');
    GotoXY (64, 20);
  End;

Var
  X,
  Y : Byte;
Begin
  If Player.Cash = 0 Then Begin
    Print ('|15No cash|07? |10House loans ya', '|07$|15' + strComma(CashStart) + '|07. |12Press a key');
    Player.Cash := CashStart;
    ReadKey;
  End;

  Print ('  |12|16Shuffling deck...', '');

  DeckShuffle;

  For Y := 1 to 2 Do
    For X := 1 to 5 Do
      DrawCard(X, Y, 3, 1);

  GotoXY (65, 10);
  Write  ('   ');
  GotoXY (65, 17);
  Write  ('   ');

  DrawCash;

  Print  ('  |15|16Enter your wager:', '  |02(|14$|15' + Int2Str(Player.Cash) + ' |14max|02)|14|17');

  EraseInput;

  Write('|17');

  Wager := Abs(Str2Int(Input(10, 10, 1, '')));

  If Wager > Player.Cash Then Wager := 0;

  If Wager = 0 Then Begin
    EraseInput;
    Exit;
  End;

  Dealer_Cards  := 1;
  Player_Cards  := 0;
  Dealer_Hidden := Deck[6].Card;

  If Dealer_Hidden = CardAce Then
    Dealer_Hidden := 11
  Else
  If Dealer_Hidden > 10 Then
    Dealer_Hidden := 10

  DrawCard(1, 2, 2, 6)

  GetNewCard(False);
  GetNewCard(False);
  GetNewCard(True);

  UpdateScores;
End;

Procedure AdjustScore (Mode: Byte);
Begin
  Case Mode of
    0 : Begin
          Player.Cash := Player.Cash - Wager;
          If Player.Cash < 0 Then Player.Cash := 0;
        End;
    1 : Begin
          Player.Cash := Player.Cash + Wager;
          If Player.Cash > 99999999 Then Player.Cash := 99999999;
        End;
  End;

  DrawCash;
End;

Var
  Ch      : Char;
  GoForIt : Boolean;
Begin
  ClrScr;

  If Graphics = 0 Then Begin
    WriteLn ('Sorry, this game requires ANSI graphics.|CR|PA');
    Halt;
  End;

  DataPath := JustPath(ProgName);

  If Upper(ParamStr(1)) = 'TOP10' Then Begin
    ExecuteTopTen;
    Halt;
  End;

  If Upper(ParamStr(1)) = 'RESET' Then Begin
    If InputYN('|CR|12Reset blackjack scores? ') Then Begin
      FileErase(DataPath + 'blackjack.ply');
      
      WriteLn ('|CRScores have been reset|CR|CR|PA');
    End;

    Halt;
  End;

  Randomize;
  DeckCreate;
  LoadPlayer;

  DispFile (DataPath + 'blackjack')
  WriteXY  (12, 23, 8, 'Mystic BlackJack v' + Version + '   Code: g00r00    Art: Grymmjack');

  DrawCash;

  Repeat
    Print ('  |15Want to play a game?', '  |10(|14Y|02/|14N|10)|08: |07')

    If OneKey('YN', False) = 'N' Then Break;

    Initialize;

    If Wager = 0 Then Continue;

    If Dealer_Score = 21 Then
      If Deck[6].Card = CardJack or Deck[7].Card = CardJack Then
        If Deck[6].Suit = SuitClub or Deck[7].Suit = SuitClub or Deck[6].Suit = SuitSpade or Deck[7].Suit = SuitSpade Then Begin
          DrawCard (1, 2, 1, 6);
          Dealer_Hidden := 0;
          AdjustScore(0);
          UpdateScores;
          Print (' |12Dealer has Black Jack', ' Press any key.');
          ReadKey
          Continue;
        End

    If Player_Score = 21 Then
      If Deck[1].Card = CardJack or Deck[2].Card = CardJack Then
        If Deck[1].Suit = SuitClub or Deck[2].Suit = SuitClub or Deck[1].Suit = SuitSpade or Deck[2].Suit = SuitSpade Then Begin
          Print (' |12Player has Black Jack', ' Press any key.');
          AdjustScore(1);
          ReadKey;
          Continue;
        End;

    Repeat
      If Player_Cards < 5 Then Begin
        Print ('|10[|14H|10]|07it, |10[|14S|10]|07tand, |10[|14Q|10]|07uit', '|08: |07');
        Ch := OneKey('HSQ', False);
      End Else
        Ch := 'S'

      Case Ch of
        'Q' : Begin
                AdjustScore(0);
                Break;
              End;
        'H' : Begin
                GetNewCard(False);

                UpdateScores;

                If Player_Score > 21 Then Begin
                  AdjustScore(0);
                  DrawCard(1,2,1,6); // show dealer hidden card
                  Print ('  |12Player busted', '  Press a key.');
                  ReadKey;
                  Break;
                End;

                // Dealer AI Rules for Hit
                // <16 = 100%
                // 16  =  50% (100 with ace as 1)
                // 17  =  25% ( 50 with ace as 1)
                // 18  =  10% ( 25 with ace as 1)
                // >18 =   0%

                Case Dealer_Score of
                  1..
                  15 : GoForIt := True;
                  16 : If Dealer_Aces = 0 Then
                         GoForIt := Random(1) = 0
                       Else
                         GoForIt := True;
                  17 : If Dealer_Aces = 0 Then
                         GoForIt := Random(3) = 0
                       Else
                         GoForIt := Random(1) = 0;
                  18 : If Dealer_Aces = 0 Then
                         GoForIt := Random(9) = 0
                       Else
                         GoForIt := Random(3) = 0;
                Else
                  GoForIt := False;    // Dealer decides to stand
                End;

                If GoForIt Then Begin
                  GetNewCard(True);
                  UpdateScores;

                  If Dealer_Score > 21 Then Begin
                    DrawCard (1, 2, 1, 6);
                    Dealer_Hidden := 0;
                    AdjustScore(1);
                    UpdateScores;
                    Print('  |12Dealer busted', '  Press a key.');
                    ReadKey;
                    Break;
                  End;
                End;
              End;
        'S' : Begin
                DrawCard (1, 2, 1, 6);
                Dealer_Hidden := 0;
                UpdateScores;

                While Dealer_Score < Player_Score and Dealer_Score < 22 and Dealer_Cards < 5 Do Begin
                  GetNewCard(True);
                  UpdateScores;
                End

                If Dealer_Score > 21 Then Begin
                  AdjustScore(1);
                  Print('  |12Dealer busted', '  Press a key.');
                  ReadKey;
                End Else
                If Player_Score > 21 Then Begin
                  AdjustScore(0);
                  Print('  |12Player busted', '  Press a key.');
                  ReadKey;
                End Else
                If Player_Score > Dealer_Score Then Begin
                  AdjustScore(1);
                  Print('  |12Player wins!', '  Press a key.');
                  ReadKey;
                End Else
                If Dealer_Score > Player_Score Then Begin
                  AdjustScore(0);
                  Print('  |12Dealer wins!', '  Press a key.');
                  ReadKey;
                End Else Begin
                  AdjustScore(2);
                  Print('  |12Push. No winner.', '  Press a key.');
                  ReadKey;
                End;

                Break;
              End;
      End;
    Until False;
  Until False;

  SavePlayer;

  ExecuteTopTen;
End.

