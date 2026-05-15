// ==========================================================================
//   File: RUMORS.MPS
//   Desc: Rumors engine for Mystic BBS v1.10
// Author: g00r00
// ==========================================================================
//
// INSTALLATION:
//
//   1) Copy RUMORS.MPS into its own directory or your scripts path and
//      compile it with MPLC or MIDE
//
//   2) For each menu you want to display rumors on, you must edit with the
//      MCFG -> Menu Editor and add the following menu command:
//
//         HotKey: EVERY
//        Command: GX (Execute MPL)
//           Data: rumors show
//
//      Note that if you have it in a path other than the scripts path, then
//      you will have to specify that in the Data field above.  For example
//      <path>rumors show
//
//      This MPL will create a rumors.dat file in the same directory where
//      you have located the compiled MPX.
//
//   3) When rumors show is ran, it generates a rumor and stores it into
//      the &1 MCI code.  Therefore, you will need to edit your menu prompt
//      or ANSI to include |&1 into it where you want it to display the rumor
//
//      If for some reason to are auto executing other functions which use
//      PromptInfo MCI codes (specially &1) you will want to add your EVERY
//      execution of this MPL program AFTER those, so that the last value
//      assigned to the MCI code was done by the rumor engine.
//
//   4) There are options in addition to the SHOW command in which you can
//      use to add other functionality to your BBS.  They are:
//
//        ADD : Allows adding of a rumor to the rumor database.  The database
//              keeps the 50 most currently added rumors.
//
//      EXAMPLE:
//
//         Hotkey: A
//        Command: GX (Execute MPL)
//           Data: rumors add
//
// CUSTOMIZATION:
//
//   If you wish to customize the prompts used in the Rumors, you can do
//   so by changing the PromptAdd and PromptSave values set below.  Do
//   whatever you want with this.  It was developed to demonstration IPLC
//   which is one of MPL's alternative syntax options.
//
// ==========================================================================

{$syntax iplc}

const
  // Prompts used
  PromptAdd  = "|CR|15E|07n|08ter |07y|08our |07r|08umor|CR:|07"
  PromptSave = "|CR|15S|07a|08ve |07t|08his |07r|08umor? |XX"

  // max number of characters for a rumor
  rumorSize  = 78;

proc rumoradd {
  @ string str
  @ string(50) data
  @ byte datasize, count
  @ file f

  write(promptadd)
  str = input(rumorSize, rumorSize, 1, "")

  if str == "" exit
  if !inputyn(promptsave) exit

  fassign (f, justpath(progname) + "rumors.dat", 2)
  freset  (f);

  if ioresult != 0 frewrite(f);

  while !feof(f) && datasize < 50 {
    datasize = datasize + 1
    freadln(f, data(datasize))
  }

  fclose(f)

  if datasize == 50 {
    for count = 1 to 49
      data(count) = data(count+1)
  } else
    datasize = datasize + 1

  data(datasize) = str

  frewrite(f)
  for count = 1 to datasize
    fwriteln(f, data(count));

  fclose(f)
}

proc rumorshow {
  @ string(50) data
  @ byte datasize, count
  @ file f

  fassign (f, justpath(progname) + "rumors.dat", 2)
  freset (f)

  if ioresult != 0 exit

  while !feof(f) && datasize < 50 {
    datasize = datasize + 1
    freadln(f, data(datasize))
  }

  count    = random(datasize) + 1
  datasize = 0

  freset(f)
  while datasize != count {
    datasize = datasize + 1
    freadln(f, data(datasize))
  }

  fclose(f)

  setpromptinfo(1, data(datasize))
}

{
  @ string options = upper(progparams);

  if pos("ADD",  options) > 0
    rumoradd()
  else
  if pos("SHOW", options) > 0
    rumorshow()
  else
    writeln("RUMORS: Invalid option: press a key|PN")
}
