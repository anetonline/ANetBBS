// Comment test

/*
  Comment test! /* comments */ (* comments *)
  // more comments

*/

(*
  comment test  (* embedded comments *) /* embedded comments */
  // more comments
*)

procedure testcase;
var
  number : longint;
  num2   : longint;
  num3   : longint;
  num4   : real;
  ch1    : char;
  str1   : string[20];
begin
  write ('Testing CASE statement... ')

  number := 73;
  num2   := 13;
  num3   := -1;
  num4   := 12.12;
  ch1    := 'A';
  str1   := 'hello';

  case number of
    68     : begin
               writeln('number is 68!');
             end
    69     : writeln('number is 69!');
    70, 71 : writeln('number is 70 or 71');
    72..80 : begin
               case num2 of
                 10 : writeln('num2 = 10');
                 11 : begin
                        writeln('num2 = 11');
                      end;
                 13 : case num3 of
                       -1: begin
                             case num4 of
                               12.12: begin
                                        case ch1 of
                                          'A' : case str1 of
                                                  'hello' : writeln('PASSED');
                                                end;
                                        end;
                                      end;
                             end;
                           end;
                      end;
               else
                 writeln('num2 is something else');
               end;
             end;
  else
    writeln('number is not found!');
  end;
end;

procedure testnumbers;
var
  num1,
  num2 : longint;
  num3 : array[1..10] of byte;
  num4 : array[1..10, 1..10, 1..10] of byte;
  num5 : longint;
begin
  write ('Testing NUMBERS... ');

  num1        := 2 + 12 * 2;
  num2        := -10;
  num3[1]     := 50;
  num4[1,1,1] := (6 - 1) + 5 * 4;
  num5        := 10 % 2 ^ 3;       // 2 to 3rd is 8, 10 modulus 8 = 2

  // floating point, mods, powers, PEDMAS, etc...

  if (num2 = -10) and (num1 = 26) and (num2 = -10) and (num3[1] = 50) and
     (num4[1,1,1] = 25) and (num5 = 2) then
    writeln('PASSED')
  else
    writeln('FAILED');
end;

procedure testrecords;
type
  testrec = record                 // total 502 bytes:
    x : byte;
    y : byte;
    d : array[1..10,1..5] of string[9];
  end;

var
  test   : array[1..2] of testrec;
  test1  : testrec;
  test2  : testrec;
  passed : boolean = false;

begin
  Write ('Testing RECORDS... ');

  test[1].d[10,5] := 'test1';
  test[2].x       := 1;
  test[2].y       := 2;
  test[2].d[1,1]  := 'hi';
  test[2].d[2,1]  := 'hello'

  if (test[1].d[10,5][1] = 't') and (test[2].x = 1) and (test[2].y = 2) and
     (test[2].d[1,1] = 'hi') and (test[2].d[2,1] = 'hello') then
       passed := true;

  if passed then begin
    test1.x      := 1;
    test1.y      := 2;
    test1.d[1,1] := 'hi';
    test1.d[2,1] := 'hello';

    test2   := test1;
    test[1] := test2;

    passed := (test1.x = test2.x) and (test1.y = test2.y) and
              (test1.d[1,1] = test2.d[1,1]) and (test1.d[2,1] = test2.d[2,1]) and
              (test[1].x = test2.x) and (test[1].y = test2.y);
  end;

  if passed then
    writeln ('PASSED')
  else
    writeln ('FAILED');

end;

procedure testprocedures;

  procedure testproc1;

    procedure testproc2
    begin
      WriteLn ('PASSED')
    end;

  begin
    testproc2
  end;

begin
  Write ('Testing PROCEDURES... ');
  testproc1;
end;

procedure testrecursive (loop:byte)
begin
  If loop = 255 then
    write('Testing RECURSIVE...');

  loop := loop - 1;

  if loop > 1 then
    testrecursive(loop)
  else
    writeln('PASSED')
end;

procedure testfunctions;

  function testfunc1 (p1,p2:byte; p3:string) : byte;
  begin
    if (p1 <> 10) or (p2 <> 5) or (p3 <> 'hello') then
      testfunc1 := 5
    else
      testfunc1 := 10;
  end;

{$syntax iplc}
  func testfunc2 : string {
    testfunc2 = "ok"
  }

{$syntax pascal}

begin
  Write ('Testing FUNCTIONS... ');

  if (testfunc1(10, 5, 'hello') = 10) and (testfunc2 = 'ok') then
    writeln ('PASSED')
  else
    writeln ('FAILED')
end;

procedure testvarpassing;

  procedure testit (var str: string);
  begin
    str := str + ' world';
  end;

var
  str : string;
begin
  write ('Testing VARPASSING... ');
  str := 'hello';
  testit(str);
  if str = 'hello world' then
    writeln ('PASSED')
  else
    writeln ('FAILED');
end;

procedure teststringindex;
var
  str : string;
begin
  write ('Testing STRING IDX...');
  str := 'hello world';
  str[6] := #33;
  if (str[1] = str[1]) and (str[2] = #101) and (str[6] = '!') then
    writeln ('PASSED')
  else
    writeln ('FAILED')
end;

procedure testloops;
var
  count1 : byte;
  count2 : byte;
  count3 : byte;
  count4 : byte;
  count5 : byte;
  loop1  : byte;
  loop2  : byte;
begin
 Write ('Testing LOOPS...');

 count1 := 0;

 while count1 < 100 do begin
   count1 := count1 + 1;
   if count1 < 5 then continue;
   if count1 < 5 then writeln('FAIL');
   if count1 = 10 then break;
 end;

 count2 := 0;

 repeat
  count2 := count2 + 1;
  if count2 < 5 then continue;
  if count2 < 5 then writeln('FAIL');
  if count2 = 10 then break;
 until count2 = 100;

 for count3 := 1 to 100 do begin
   if count3 < 5 then continue;
   if count3 < 5 then writeln('FAIL');
   if count3 = 10 then break;
 end;

 loop1 := 0;

 for count4 := 1 to 10 do begin
   count4 := 10;
   loop1  := loop1 + 1;
 end;

 loop2 := 0;

 for count5 := 10 downto 1 do begin
   count5 := 1;
   loop2  := loop2 + 1;
 end;

 if (count1 = 10) and (count2 = 10) and (count3 = 10) and (count4 = 10) and
    (loop1 = 1) and (count5 = 1) and (loop2 = 1) then
   writeln ('PASSED')
 else
   writeln ('FAILED');
end;

procedure testconsts;
const
  const1 = 'hello';
  const2 = true;
  const3 = 555;
  const4 = 'A';
var
  str1 : string;
  bol1 : boolean;
  ch1  : char;
  num1 : longint;
  ok1  : boolean;
  ok2  : boolean;
  ok3  : boolean;
  ok4  : boolean;
begin
  write ('Testing CONSTS...');

  ok1  := false;
  ok2  := false;
  ok3  := false;
  ok4  := false;

  str1 := 'hello';
  bol1 := true;
  num1 := 555;
  ch1  := 'A'

  case str1 of
    const1 : ok1 := true;
  end;

  case bol1 of
    const2 : ok2 := true;
  end;

  case num1 of
    const3 : ok3 := true;
  end;

  case ch1 of
    const4 : ok4 := true;
  end;

  if ok1 and ok2 and ok3 and ok4 then
    writeln ('PASSED')
  else
    writeln ('FAILED')
end;

procedure testsyntaxparsing;

{$syntax iplc}  // Iniquity-like syntax for the oldskool or maybe C-heads
                // been thinking about moving it to be closer to javascript
                // than IPL?

  proc testiplc {
    @ byte test1, test2, test3 = 10;
    write ("PASS");
    @ string anywhere = "we can do this wherever..."
  }

{$syntax pascal}

  procedure testpascal;
  var
    test1, test2, test3 : byte = 10;  // not a pascal standard!
  begin
    writeln('ED');
    var anywhere : string = 'wait!  pascal doesn''t allow this!';
  end;

begin
  write ('Testing SYNTAX... ');
  testiplc;
  testpascal;
end;

procedure testfileio;
const
  fmReadWriteDenyNone = 66;
var
  f : file;
  b : array[1..11] of Char;
  s : string[20];
  l : longint;
begin
  write ('Testing FILEIO... ');

  // file IO is completely random.  no text/file crap like in pascal
  // but it operates very close to pascal, just easier.  splitting the
  // fOpen into fassign/frewrite/freset allows us to not have to open
  // and close files constantly to reset or recreate it as in MPL 1.
  // And doing away with raw numbers and adding a File type makes things
  // much more manageable (and gives us virtually unlimited files)

  fassign  (f, 'testmps.dat', fmReadWriteDenyNone);
  frewrite (f);
  fwriteln (f, 'Hello world');

  freset   (f);
  fread    (f, b[1], 11);

  freset   (f);
  freadln  (f, s);

  freset   (f);
  fseek    (f, fsize(f));

  if not feof(f) or fpos(f) <> fsize(f) then begin
    writeln('FAILED');
    fclose(f);
    exit;
  end;

  fclose (f);

  if fileexist('testmps.dat') then fileerase('testmps.dat');

  if ioresult <> 0 or fileexist('testmps.dat') then begin
    writeln('FAILED');
    exit;
  end;

  // we can read data directly in to char arrays or strings as if it were
  // a char array.  no problems with reading non-pascal structs.

  if b[1] = 'H' and b[2] = 'e' and b[3] = 'l' and s = 'Hello world' then
    writeln('PASSED')
  else
    writeln('FAILED');
end;

procedure testrecordfileIO;
type
  myuserrecord = record
    username  : string[30];
    somevalue : array[1..5] of byte;
  end;

var
  f : file;
  u : myuserrecord;
  a : byte;
begin
  Write ('Testing RECORDFILEIO... ');

  u.username := 'testuser';

  for a := 1 to 5 do
    u.somevalue[a] := 1;

  fassign   (f, 'testmps.dat', 66);
  frewrite  (f);
  fwriterec (f, u);

  fillchar(u, sizeof(u), #0);

  freset   (f);
  freadrec (f, u);
  fclose   (f);

  if fileexist('testmps.dat') then fileerase('testmps.dat');

  if (u.username = 'testuser') and (u.somevalue[1] = 1) and (u.somevalue[2] = 1) and
     (u.somevalue[3] = 1) and (u.somevalue[4] = 1) and (u.somevalue[5] = 1) then
       writeln('PASSED')
  else
       writeln('FAILED');
end;

begin
  writeln ('|07|16|CLMystic BBS Programming Language Test Module');
  writeln ('');

  testcase;
  testnumbers;
  testrecords;
  testprocedures;
  testfunctions;
  testrecursive(255);
  testvarpassing;
  teststringindex;
  testloops;
  testconsts;
  testsyntaxparsing;
  testfileio;
  testrecordfileio;

  writeln('|CRAll tests complete.  Press a key.|PN');
end
