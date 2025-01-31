module VoxLogicA.Main

open System.Reflection
open Argu

type LoadFlags = { fname: string; numCores: int }
// type JSonOutput = FSharp.Data.JsonProvider<"example.json">
type CmdLine =
    | [<UniqueAttribute>] Version
    | [<UniqueAttribute>] SaveTaskGraphAsDot of string
    | [<UniqueAttribute>] SaveTaskGraph of option<string>
    | [<UniqueAttribute>] SaveTaskGraphAsAST of option<string>
    | [<UniqueAttribute>] SaveTaskGraphAsProgram of option<string>
    | [<UniqueAttribute>] SaveSyntax of option<string>
    | [<MainCommandAttribute; UniqueAttribute>] Filename of string

    interface Argu.IArgParserTemplate with
        member s.Usage =
            match s with
            | Version -> "print the voxlogica version and exit"
            | SaveTaskGraph _ -> "save the task graph"
            | SaveTaskGraphAsDot _ -> "save the task graph in .dot format and exit"
            | SaveTaskGraphAsAST _ -> "save the task graph in AST format and exit"
            | SaveTaskGraphAsProgram _ -> "save the task graph in VoxLogicA format and exit"
            | SaveSyntax _ -> "save the AST in text format and exit"
            | Filename _ -> "VoxLogicA session file"

[<EntryPoint>]
let main (argv: string array) =
    let name = Assembly.GetEntryAssembly().GetName()
    let version = name.Version

    let informationalVersion =
        ((Assembly.GetEntryAssembly().GetCustomAttributes(typeof<AssemblyInformationalVersionAttribute>, false).[0])
        :?> AssemblyInformationalVersionAttribute)
            .InformationalVersion

    let cmdLineParser =
        ArgumentParser.Create<CmdLine>(programName = name.Name, errorHandler = ProcessExiter())

    let parsed = cmdLineParser.Parse argv

    if Option.isSome (parsed.TryGetResult Version) then
        printfn "%s" informationalVersion
        exit 0

    ErrorMsg.Logger.LogToStdout()
#if ! DEBUG
    ErrorMsg.Logger.SetLogLevel([ "user"; "info" ])
#else
    ()
#endif

    if version.Revision <> 0 then
        ErrorMsg.Logger.Warning(
            sprintf
                "You are using a PRERELEASE version of %s. The most recent stable release is %d.%d.%d."
                name.Name
                version.Major
                version.Minor
                version.Build
        )

    try

        let filename: string =
            if parsed.Contains Filename then
                parsed.GetResult Filename
            else
#if DEBUG
                "test.imgql"
#else
                printfn "%s version: %s" name.Name informationalVersion
                printfn "%s\n" (cmdLineParser.PrintUsage())
                exit 0
#endif

        ErrorMsg.Logger.Info $"{name.Name} version: {informationalVersion}"

        let syntax = Parser.parseProgram filename
        ErrorMsg.Logger.Debug "Program parsed"

        if parsed.Contains SaveSyntax then
            let filenameOpt = parsed.GetResult SaveSyntax

            match filenameOpt with
            | Some filename ->
                ErrorMsg.Logger.Debug $"Saving the abstract syntax to {filename}"
                System.IO.File.WriteAllText(filename, $"{syntax}")
            | None -> ErrorMsg.Logger.Debug $"{syntax}"

        let program: Reducer.WorkPlan = Reducer.reduceProgram syntax

        ErrorMsg.Logger.Debug "Program reduced"
        ErrorMsg.Logger.Info $"Number of tasks: {program.operations.Length}"


        if parsed.Contains SaveTaskGraphAsAST then
            let filenameOpt = parsed.GetResult SaveTaskGraphAsAST

            let voxlogicaProgram = program.ToProgram("n")

            match filenameOpt with
            | Some filename ->
                ErrorMsg.Logger.Debug $"Saving the task graph in AST syntax to {filename}"
                System.IO.File.WriteAllText(filename, $"{voxlogicaProgram}")
            | None -> ErrorMsg.Logger.Debug $"{voxlogicaProgram}"

        if parsed.Contains SaveTaskGraphAsProgram then
            let filenameOpt = parsed.GetResult SaveTaskGraphAsProgram

            let voxlogicaProgram = program.ToProgram("n")
            let voxlogicaSyntax = voxlogicaProgram.ToSyntax()

            match filenameOpt with
            | Some filename ->
                ErrorMsg.Logger.Debug $"Saving the task graph in VoxLogicA syntax to {filename}"
                System.IO.File.WriteAllText(filename, $"{voxlogicaSyntax}")
            | None -> ErrorMsg.Logger.Debug $"{voxlogicaSyntax}"

        if parsed.Contains SaveTaskGraph then
            let filenameOpt = parsed.GetResult SaveTaskGraph

            match filenameOpt with
            | Some filename ->
                ErrorMsg.Logger.Debug $"Saving the task graph to {filename}"
                System.IO.File.WriteAllText(filename, $"{program}")
            | None -> ErrorMsg.Logger.Debug $"{program}"


        if parsed.Contains SaveTaskGraphAsDot then
            let filename = parsed.GetResult SaveTaskGraphAsDot
            ErrorMsg.Logger.Debug $"Saving the task graph to {filename}"
            System.IO.File.WriteAllText(filename, program.ToDot())

        ErrorMsg.Logger.Info "All done."
        0
    with e ->
        ErrorMsg.Logger.DebugExn e
        raise e
        1
