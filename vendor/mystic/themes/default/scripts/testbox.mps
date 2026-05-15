// =========================================================================
// TESTBOX.MPS : MPL example of using the ANSI box class functions
// =========================================================================

Procedure SetBoxDefaults (Handle: LongInt; Header: String);
Begin
  // Mystic boxes default to the grey 3D style boxes used in the
  // configuration, but you can change all aspects of them if you want
  // to using the functions below.

  If Header <> '' Then
    BoxHeader (Handle,      // Box class handle
               0,           // Header justify (0=center, 1=left, 2=right)
               31,          // Header attribute
               Header);     // Header text

  // Available Box Frame types:
  //
  // 1 = ⁄ƒø≥≥¿ƒŸ
  // 2 = …Õª∫∫»Õº
  // 3 = ÷ƒ∑∫∫”ƒΩ
  // 4 = ’Õ∏≥≥‘Õæ
  // 5 = €ﬂ€€€€‹€
  // 6 = €ﬂ‹€€ﬂ‹€
  // 7 =
  // 8 = .-.||`-'

  // Box shadows (if enabled) will actually read the characters under them
  // and shade them using the shadow attribute.

  BoxOptions (Handle,       // Box class handle
              2,            // Box frame type (1-8)
              False,        // Use "3D" box shading effect
              8,            // Box attribute
              8,            // Box 3D effect attr1 (if on)
              8,            // Box 3D effect attr2 (if on)
              8,            // Box 3D effect attr3 (if on)
              True,         // Use box shadowing
              112);         // Box shadow attribute
End;

Var
  BoxHandle : LongInt;
Begin
  PurgeInput;

  ClrScr;

  WriteXY (20, 5, 12, 'This is a line of text that will have a window');
  WriteXY (20, 6, 12, 'drawn over top of it.  Press a key to draw a box');

  ReadKey;

  ClassCreate (BoxHandle, 'box');

  BoxOpen (BoxHandle,       // Box class handle
           20,              // top X corner of box
           5,               // top Y corner of box
           60,              // bottom X corner of box
           10);             // bottom Y corner of box

  WriteXY (1, 1, 15, 'Press any key to close the box');
  WriteXY (1, 2, 15, 'The screen contents under the box will be restored!');

  ReadKey;

  // Closing a box will restore what was "under" it on the screen before the
  // box was created.  You do not HAVE to close boxes if you dont want to.

  BoxClose (BoxHandle);

  WriteXY (1, 11, 11, 'Now lets change the box values.  Press a key');

  ReadKey;

  // Now lets change the defaults to the box and open another one

  SetBoxDefaults (BoxHandle, ' My Window Header ');

  BoxOpen (BoxHandle, 20, 5, 60, 10);

  ReadKey;

  BoxClose  (BoxHandle);
  ClassFree (BoxHandle);

  WriteXY (1, 14, 10, 'Pretty cool huh?  Press a key to exit.');

  ReadKey;
End.
