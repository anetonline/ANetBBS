// Ported from Iniquity's Turbo Pascal source code to Mystic Script
// Original code by Mike Fricker port by g00r00 as proof of concept for Mystic Script

Program Tetris;

Const
  maxGridY  = 50;
  maxGridX  = 40;
  maxPlayer = 2;
  maxPtY    = 5;
  maxPtX    = 5;
  maxShape  = 7;
  maxLevel  = 18;

Type
  PGrid = ^TGrid;
  TGrid = Array[1..MaxGridY] of Array[1..MaxGridX] of Byte;

  TPlayer = Record
    Local   : Boolean;
    Name    : String[36];
    Grid    : pGrid;
    Idx     : array[1..maxGridY] of Byte;
    gXp     : Byte;
    gYp     : Byte;
    gXs     : Byte;
    gYs     : Byte;
    gYl     : Byte;
    curB    : Byte;
    curF    : Byte;
    bX      : Integer;
    bY      : Integer;
    Lines   : Word;
    Level   : Byte;
    Score   : LongInt;
    nShape  : Byte;
    lastM   : Double;
  End;

  TShape = Array[1..4] of Array[1..maxPtY] of Array[1..maxPtX] of Byte;

Const
  sPts : Array[1..maxShape] of Byte = [4,6,6,5,6,5,5];
  S    : Array[1..maxShape] of TShape =
      [[[[0,0,1,0,0],
         [0,0,1,0,0],      {   Û   }
         [0,0,1,0,0],      {   Û   }
         [0,0,1,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,0,0,0,0],      {       }
         [0,1,1,1,1],      {  ßßßß }
         [0,0,0,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,0,1,0,0],      {   Ü   }
         [0,0,1,0,0],      {   Û   }
         [0,0,1,0,0],      {   ß   }
         [0,0,1,0,0]],

        [[0,0,0,0,0],
         [0,0,0,0,0],      {       }
         [1,1,1,1,0],      { ßßßß  }
         [0,0,0,0,0],      {       }
         [0,0,0,0,0]]],

       [[[0,0,1,0,0],
         [0,0,1,0,0],      {   Û   }
         [0,1,1,0,0],      {  ßß   }
         [0,0,0,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,0,1,0,0],      {   Ü   }
         [0,0,1,1,1],      {   ßßß }
         [0,0,0,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,0,0,0,0],      {       }
         [0,0,1,1,0],      {   Ûß  }
         [0,0,1,0,0],      {   ß   }
         [0,0,1,0,0]],

        [[0,0,0,0,0],
         [0,0,0,0,0],      {       }
         [1,1,1,0,0],      { ßßÛ   }
         [0,0,1,0,0],      {       }
         [0,0,0,0,0]]],

       [[[0,0,1,0,0],
         [0,0,1,0,0],      {   Û   }
         [0,0,1,1,0],      {   ßß  }
         [0,0,0,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,0,0,0,0],      {       }
         [0,0,1,1,1],      {   Ûßß }
         [0,0,1,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,0,0,0,0],      {       }
         [0,1,1,0,0],      {  ßÛ   }
         [0,0,1,0,0],      {   ß   }
         [0,0,1,0,0]],

        [[0,0,0,0,0],
         [0,0,1,0,0],      {   Ü   }
         [1,1,1,0,0],      { ßßß   }
         [0,0,0,0,0],      {       }
         [0,0,0,0,0]]],

       [[[0,0,0,0,0],
         [0,0,1,0,0],      {   Ü   }
         [0,1,1,1,0],      {  ßßß  }
         [0,0,0,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,0,1,0,0],      {   Ü   }
         [0,0,1,1,0],      {   Ûß  }
         [0,0,1,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,0,0,0,0],      {       }
         [0,1,1,1,0],      {  ßÛß  }
         [0,0,1,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,0,1,0,0],      {   Ü   }
         [0,1,1,0,0],      {  ßÛ   }
         [0,0,1,0,0],      {       }
         [0,0,0,0,0]]],

       [[[0,0,0,0,0],
         [0,1,1,0,0],      {  ÜÜ   }
         [0,1,1,0,0],      {  ßß   }
         [0,0,0,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,1,1,0,0],      {  ÜÜ   }
         [0,1,1,0,0],      {  ßß   }
         [0,0,0,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,1,1,0,0],      {  ÜÜ   }
         [0,1,1,0,0],      {  ßß   }
         [0,0,0,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,1,1,0,0],      {  ÜÜ   }
         [0,1,1,0,0],      {  ßß   }
         [0,0,0,0,0],      {       }
         [0,0,0,0,0]]],

       [[[0,0,0,0,0],
         [0,0,1,0,0],      {   Ü   }
         [0,0,1,1,0],      {   ßÛ  }
         [0,0,0,1,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,0,0,0,0],      {       }
         [0,0,1,1,0],      {  ÜÛß  }
         [0,1,1,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,1,0,0,0],      {  Ü    }
         [0,1,1,0,0],      {  ßÛ   }
         [0,0,1,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,0,0,0,0],      {       }
         [0,0,1,1,0],      {  ÜÛß  }
         [0,1,1,0,0],      {       }
         [0,0,0,0,0]]],

       [[[0,0,0,0,0],
         [0,0,1,0,0],      {   Ü   }
         [0,1,1,0,0],      {  Ûß   }
         [0,1,0,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,1,1,0,0],      {  ÜÜ   }
         [0,0,1,1,0],      {   ßß  }
         [0,0,0,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,0,1,0,0],      {   Ü   }
         [0,1,1,0,0],      {  Ûß   }
         [0,1,0,0,0],      {       }
         [0,0,0,0,0]],

        [[0,0,0,0,0],
         [0,1,1,0,0],      {  ÜÜ   }
         [0,0,1,1,0],      {   ßß  }
         [0,0,0,0,0],      {       }
         [0,0,0,0,0]]]];

   Lev : Array[1..maxLevel] of Record T: Single; L: Word; End =
          [[0.60,0],
           [0.55,10],
           [0.50,20],
           [0.45,30],
           [0.40,40],
           [0.32,50],
           [0.30,60],
           [0.27,70],
           [0.23,80],
           [0.20,90],
           [0.18,100],
           [0.15,110],
           [0.13,120],
           [0.10,130],
           [0.08,150],
           [0.05,160],
           [0.04,175],
           [0.02,200]];

function dtTimer : Single;
var
    year,
    month,
    day,
    yr,
    dayofweek,
    hour,
    minute,
    second,
    sec100  : Word;
begin
   GetDate(year, month, day, dayofweek);
   GetTime(hour, minute, second, sec100);
   result := (day*24*60*60) + ((hour*60*60) + (minute*60) + (second) + (sec100 * 0.01));
end;
	
function dtRealDiff(before, after : Single) : Single;
begin
   if after > before then
     result := after-before
  else
    result := 0;
end;

type
   tColorRec = record
      Fore  : Byte;
      Back  : Byte;
      Blink : Boolean;
   end;

var
   P : array[1..maxPlayer] of tPlayer;
   numP, q : Byte;
   itdone, Ans : Boolean;
   iCh : Char;
   sfPos, sfPos2: array[1..30] of record
                     Ok : Boolean;
                     X, Y : Byte;
                     C : tColorRec;
                  end;

procedure itWriteScore(n : Byte);
begin
  GotoXY (11, 15);
  Write('|11' + Comma(P[n].Score) + '|09');
end;

procedure itWriteLines(n : Byte);
begin
   GotoXY (11, 14);
   Write('|11' + int2str(P[n].Lines) + '|09');
end;

procedure itWriteLevel(n : Byte);
begin
   GotoXY(11, 12);
   Write('|11' + int2str(P[n].Level) + '|09');
end;

procedure itDrawBlock(n : Byte; Erase : Boolean);
var z, x, y, yp : Integer; top : Boolean; ch : Char;
begin
   
   //oSetColRec(sfPos[1].C);
   with P[n] do
   begin
      for x := 1 to maxPtX do for y := 1 to maxPtY do if s[curB,curF,y,x] = 1 then
      begin
         yp := Idx[bY+y-1];
         top := Odd(bY+y-1);
         GotoXY(gXp+bX+x-2,gYp+yp-1);
         if Erase then Grid^[bY+y-1,bX+x-1] := 0 else
         begin
            if Grid^[bY+y-1,bX+x-1] = 1 then itDone := True;
            Grid^[bY+y-1,bX+x-1] := 1;
         end;
         if top then
         begin
            if (Grid^[bY+y-1,bX+x-1] = 1) and (Grid^[bY+y,bX+x-1] = 1) then ch := 'Û' else
            if (Grid^[bY+y-1,bX+x-1] = 1) and (Grid^[bY+y,bX+x-1] = 0) then ch := 'ß' else
            if (Grid^[bY+y-1,bX+x-1] = 0) and (Grid^[bY+y,bX+x-1] = 1) then ch := 'Ü' else
                                                                            ch := ' ';
         end else
         begin
            if (Grid^[bY+y-2,bX+x-1] = 1) and (Grid^[bY+y-1,bX+x-1] = 1) then ch := 'Û' else
            if (Grid^[bY+y-2,bX+x-1] = 1) and (Grid^[bY+y-1,bX+x-1] = 0) then ch := 'ß' else
            if (Grid^[bY+y-2,bX+x-1] = 0) and (Grid^[bY+y-1,bX+x-1] = 1) then ch := 'Ü' else
                                                                              ch := ' ';
         end;
         WriteRaw(Ch);
      end;
   end;
   
   flush;
end;

procedure itUpdateLine(n, l : Byte);
var x : Integer; top : Boolean; ch : Char;
begin
   //oSetColRec(sfPos[1].C);
   top := Odd(l);
   with P[n] do
   begin
      GotoXY(gXp,gYp+Idx[l]-1);
      for x := 1 to gXs do
      begin
         if top then
         begin
            if (Grid^[l,x] = 1) and (Grid^[l+1,x] = 1) then ch := 'Û' else
            if (Grid^[l,x] = 1) and (Grid^[l+1,x] = 0) then ch := 'ß' else
            if (Grid^[l,x] = 0) and (Grid^[l+1,x] = 1) then ch := 'Ü' else
                                                            ch := ' ';
         end else
         begin
            if (Grid^[l-1,x] = 1) and (Grid^[l,x] = 1) then ch := 'Û' else
            if (Grid^[l-1,x] = 1) and (Grid^[l,x] = 0) then ch := 'ß' else
            if (Grid^[l-1,x] = 0) and (Grid^[l,x] = 1) then ch := 'Ü' else
                                                            ch := ' ';
         end;
         WriteRaw(Ch);
      end;
   end;
   
   flush;
end;

procedure itNewBlock(n : Byte);
begin
   with P[n] do
   begin
      curB := nShape;
      nShape := Random(maxShape)+1;
      curF := Random(4)+1;
      bX := Random(gXs-4)+1;
      bY := 1;
      lastM := dtTimer;
   end;
   itDrawBlock(n,False);
end;

function itZapLine(n, l : Byte) : Boolean;
var ok : Boolean; z : Byte;
begin
   result := False;
   if (l < 1) or (l > P[n].gYs) then Exit;
   ok := True;
   for z := 1 to P[n].gXs do if P[n].Grid^[l,z] = 0 then ok := False;
   result := ok;
end;

function itLineEmpty(n, l : Byte) : Boolean;
var ok : Boolean; z : Byte;
begin
   result := False;
   if (l < 1) or (l > P[n].gYs) then Exit;
   ok := True;
   for z := 1 to P[n].gXs do if P[n].Grid^[l,z] = 1 then ok := False;
   result := ok;
end;

procedure itCheckLines(n : Byte);
var y, x, b, l : Byte;
begin
   with P[n] do
   begin
      Inc(Score,sPts[curB]);
      itWriteScore(n);
      l := 0;
      for y := 1 to maxPtY do if itZapLine(n,bY+y-1) then
      begin
         Inc(l);
         for b := bY+y-1 downto 2 do Grid^[b] := Grid^[b-1];
         for x := 1 to gXs do Grid^[1,x] := 0;
         b := bY+y-1;
         Inc(Lines);
         itWriteLines(n);
      end;
      if l > 0 then
      begin
         for y := 1 to b do itUpdateLine(n,y);
         Inc(Score,(l*l)*Level*100);
         //oBeep;
         itWriteScore(n);
         if (Level < maxLevel) and (Lines >= Lev[Level+1].l) then
         begin
            Inc(Level);
            itWriteLevel(n);
            //oBeep;
            //oBeep;
         end;
      end;
   end;
end;

procedure itMoveDown(n : Byte);
var ny, x, y : Integer; stop : boolean;
begin
   with P[n] do
   begin
      ny := bY+1;
      stop := False;
      for x := 1 to maxPtX do for y := 1 to maxPtY do
         if (s[curB,curF,y,x] <> 0) and
           (((Grid^[ny+y-1,bX+x-1] = 1) and
             (not ((y < 5) and (S[curB,curF,y+1,x] = 1)))) or
            (ny+y-1 > gYs)) then stop := True;
      if not stop then
      begin
         itDrawBlock(n,True);
         bY := bY+1;
         itDrawBlock(n,False);
      end else
      begin
         itCheckLines(n);
         itNewBlock(n);
      end;
   end;
end;

procedure itFastDown(n : Byte);
var ny, x, y, oy : Integer; stop : boolean;
begin
   with P[n] do
   begin
      stop := False;
      oy := bY;
      repeat
         ny := oY+1;
         for x := 1 to maxPtX do for y := 1 to maxPtY do
            if (s[curB,curF,y,x] <> 0) and
              (((Grid^[ny+y-1,bX+x-1] = 1) and
                (not ((y < 5) and (S[curB,curF,y+1,x] = 1)))) or
               (ny+y-1 > gYs)) then stop := True;
         if not Stop then oY := oY+1;
      until stop;
      itDrawBlock(n,True);
      bY := oY;
      itDrawBlock(n,False);
      itCheckLines(n);
      itNewBlock(n);
   end;
end;

procedure itMoveRight(n : Byte);
var nx, x, y : Integer; ok : Boolean;
begin
   with P[n] do
   begin
      nx := bX+1;
      ok := True;
      for x := 1 to maxPtX do for y := 1 to maxPtY do
         if (s[curB,curF,y,x] <> 0) and
           (((Grid^[bY+y-1,nx+x-1] = 1) and
             (not ((x < 5) and (S[curB,curF,y,x+1] = 1)))) or
            (nx+x-1 > gXs)) then ok := False;
      if ok then
      begin
         itDrawBlock(n,True);
         bX := bX+1;
         itDrawBlock(n,False);
      end;
   end;
end;

procedure itMoveLeft(n : Byte);
var nx, x, y : Integer; ok : Boolean;
begin
   with P[n] do
   begin
      nx := bX-1;
      ok := True;
	  
      for x := 1 to maxPtX do
	    for y := 1 to maxPtY do
          if (s[curB,curF,y,x] <> 0) and ((nx+x-1 < 1) or ((Grid^[bY+y-1,nx+x-1] = 1) and (not ((x > 1) and (S[curB,curF,y,x-1] = 1))))) then
		    ok := False;
			
      if ok then begin
         itDrawBlock(n,True);
         bX := bX-1;
         itDrawBlock(n,False);
      end;
   end;
end;

procedure itRotate(n : Byte; add : Integer);
var nf, x, y : Integer; ok : Boolean;
begin
   with P[n] do
   begin
      nf := curF+add;
      if nf < 1 then nf := 4 else if nf > 4 then nf := 1;
      ok := True;
      for x := 1 to maxPtX do for y := 1 to maxPtY do
         if (s[curB,nf,y,x] <> 0) and
           ((bX+x-1 < 1) or
			 (bX+x-1 > gXs) or (bY+y-1 > gYs) or
		     ((Grid^[bY+y-1,bX+x-1] = 1) and
             (not (S[curB,curF,y,x] = 1)))) then ok := False;
      if ok then
      begin
         itDrawBlock(n,True);
         curF := nf;
         itDrawBlock(n,False);
      end;
   end;
end;

procedure itPlayTetris;
begin
  Write('|[0');
  ans := showfile(extractpath(scriptname) + 'tetris');
  
  sfpos[1].x := 29;
  sfpos[1].y := 11;
  sfpos[2].x := 50;
  sfpos[2].y := 22;

  FillChar(P,SizeOf(P),#0);

  itDone := False;

  numP := 1;

  with P[1] do begin
    Name := 'Test user';
    New(Grid);
    FillChar(Grid^,SizeOf(Grid^),#0);
    gYl := 0;
	
    gXp := sfPos[1].X;
    gYp := sfPos[1].Y;
    gXs := sfPos[2].X-gXp+1;
    gYs := sfPos[2].Y-gYp+1;
    gYs := gYs*2;
	  
    if Odd(gYs) then
      Dec(gYs);
  
    Local := True;
	
    for q := 1 to gYs do begin
      if Local then Inc(gYl);
	  
      Local := not Local;
      Idx[q] := gYl;
    end;

    curB := 1;
    curF := 1;
    bX := 1;
    bY := 1;
    Lines := 0;
    Score := 0;
    Level := 1;
    nShape := Random(maxShape)+1;
    Local := True;
  end;

  itWriteLevel(1);
  itWriteLines(1);
  itWriteScore(1);
  itNewBlock(1); 
  
  repeat
    iCh := InKey(25);
	
	If iCh <> #255 Then Begin 
	  if isExtended then begin
        case iCh of
          keyDown : itFastDown(1);
          keyLeft : itMoveLeft(1);
          keyRight: itMoveRight(1);
          keyUp   : itRotate(1,1);
	    end;
	  end else
        case UpCase(iCh) of
          ' ','W','X','5' : itRotate(1,1);
          #13,'2','S'     : itFastDown(1);
          'Z'             : itRotate(1,-1);
          '4','A'         : itMoveLeft(1);
          '6','D'         : itMoveRight(1);
          '0'             : itMoveDown(1);
          #27             : itDone := True;
        end;
    end;
	  
    for q := 1 to numP do with P[q] do begin
      if dtRealDiff(lastM,dtTimer) > Lev[Level].t then begin
        itMoveDown(q);
        lastM := dtTimer;
      end;
    end;  
  until (Shutdown) or (itDone);

  Write('|[1');
  
  with P[1] do begin
    Dispose(Grid);
  end;
  
  //sfGotoPos(maxPos);
  //logWrite('Played Tetris ['+St(P[1].Lines)+' lines, '+Stc(P[1].Score)+
            //' points; level '+St(P[1].Level)+']');
  //itAddHiScore;
End;

begin
  itPlayTetris;
end.