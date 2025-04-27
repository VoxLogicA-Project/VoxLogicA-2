"""
VoxLogicA Main module - Python implementation
"""

import os
import sys
import argparse
from typing import Optional, List, Dict, Any, Union
from pathlib import Path
import importlib.metadata
import uvicorn

import typer
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .parser import parse_program
from .reducer import reduce_program
from .error_msg import Logger, VLException

# Create CLI app with Typer
app = typer.Typer(help="VoxLogicA - Voxel Logic Analyzer")

# Create FastAPI app for API server
api_app = FastAPI(
    title="VoxLogicA API",
    description="API for VoxLogicA, a Voxel Logic Analyzer",
    version="0.1.0",
)


def get_version() -> str:
    """Get the version of the VoxLogicA package"""
    try:
        return importlib.metadata.version("voxlogica")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0-dev"


# ----------------- CLI Functions -----------------


@app.command()
def version():
    """Print the VoxLogicA version and exit"""
    typer.echo(f"VoxLogicA version: {get_version()}")


@app.command()
def run(
    filename: str = typer.Argument(..., help="VoxLogicA session file"),
    save_task_graph: Optional[str] = typer.Option(None, help="Save the task graph"),
    save_task_graph_as_dot: Optional[str] = typer.Option(
        None, help="Save the task graph in .dot format and exit"
    ),
    save_task_graph_as_ast: Optional[str] = typer.Option(
        None, help="Save the task graph in AST format and exit"
    ),
    save_task_graph_as_program: Optional[str] = typer.Option(
        None, help="Save the task graph in VoxLogicA format and exit"
    ),
    save_syntax: Optional[str] = typer.Option(
        None, help="Save the AST in text format and exit"
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug mode"),
):
    """Run a VoxLogicA session"""
    # Set up logging
    Logger.log_to_stdout()
    if debug:
        Logger.set_log_level(["user", "info", "warn", "fail", "dbug"])
    else:
        Logger.set_log_level(["user", "info", "warn", "fail"])

    Logger.info(f"VoxLogicA version: {get_version()}")

    try:
        # Parse the program
        syntax = parse_program(filename)
        Logger.debug("Program parsed")

        # Save syntax if requested
        if save_syntax is not None:
            if save_syntax:
                Logger.debug(f"Saving the abstract syntax to {save_syntax}")
                with open(save_syntax, "w") as f:
                    f.write(str(syntax))
            else:
                Logger.debug(f"{syntax}")

        # Reduce the program
        program = reduce_program(syntax)
        Logger.debug("Program reduced")
        Logger.info(f"Number of tasks: {len(program.operations)}")

        # Save task graph as AST if requested
        if save_task_graph_as_ast is not None:
            voxlogica_program = program.to_program()
            if save_task_graph_as_ast:
                Logger.debug(
                    f"Saving the task graph in AST syntax to {save_task_graph_as_ast}"
                )
                with open(save_task_graph_as_ast, "w") as f:
                    f.write(str(voxlogica_program))
            else:
                Logger.debug(f"{voxlogica_program}")

        # Save task graph as program if requested
        if save_task_graph_as_program is not None:
            voxlogica_program = program.to_program()
            voxlogica_syntax = voxlogica_program.to_syntax()
            if save_task_graph_as_program:
                Logger.debug(
                    f"Saving the task graph in VoxLogicA syntax to {save_task_graph_as_program}"
                )
                with open(save_task_graph_as_program, "w") as f:
                    f.write(voxlogica_syntax)
            else:
                Logger.debug(f"{voxlogica_syntax}")

        # Save task graph if requested
        if save_task_graph is not None:
            if save_task_graph:
                Logger.debug(f"Saving the task graph to {save_task_graph}")
                with open(save_task_graph, "w") as f:
                    f.write(str(program))
            else:
                Logger.debug(f"{program}")

        # Save task graph as DOT if requested
        if save_task_graph_as_dot is not None:
            Logger.debug(f"Saving the task graph to {save_task_graph_as_dot}")
            with open(save_task_graph_as_dot, "w") as f:
                f.write(program.to_dot())

        Logger.info("All done.")
        return 0

    except VLException as e:
        Logger.failure(str(e))
        if debug:
            Logger.debug_exception(e)
        return 1

    except Exception as e:
        Logger.failure(f"Unexpected error: {str(e)}")
        if debug:
            Logger.debug_exception(e)
        return 1


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Host to bind the API server"),
    port: int = typer.Option(8000, help="Port to bind the API server"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug mode"),
):
    """Start the VoxLogicA API server"""
    # Set up logging
    Logger.log_to_stdout()
    if debug:
        Logger.set_log_level(["user", "info", "warn", "fail", "dbug"])
    else:
        Logger.set_log_level(["user", "info", "warn", "fail"])

    Logger.info(
        f"Starting VoxLogicA API server version {get_version()} on {host}:{port}"
    )
    uvicorn.run(api_app, host=host, port=port)


# ----------------- API Models -----------------


class ProgramRequest(BaseModel):
    """Request to parse and reduce a VoxLogicA program"""

    program: str = Field(..., description="The VoxLogicA program to parse and reduce")
    filename: Optional[str] = Field(
        None, description="Optional filename for error reporting"
    )


class ProgramResponse(BaseModel):
    """Response with the parsed and reduced program"""

    operations: int = Field(
        ..., description="Number of operations in the reduced program"
    )
    goals: int = Field(..., description="Number of goals in the reduced program")
    task_graph: str = Field(..., description="String representation of the task graph")
    dot_graph: Optional[str] = Field(
        None, description="DOT representation of the task graph"
    )
    syntax: Optional[str] = Field(None, description="Original program syntax")


class ErrorResponse(BaseModel):
    """Error response"""

    error: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Additional error details")


# ----------------- API Endpoints -----------------


@api_app.get("/version")
def api_version() -> Dict[str, str]:
    """Get the VoxLogicA version"""
    return {"version": get_version()}


@api_app.post("/program", response_model=Union[ProgramResponse, ErrorResponse])
def api_program(request: ProgramRequest) -> Dict[str, Any]:
    """Parse and reduce a VoxLogicA program"""
    try:
        # Write the program to a temporary file if no filename is provided
        if request.filename:
            filename = request.filename
            with open(filename, "w") as f:
                f.write(request.program)
        else:
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".imgql", delete=False) as temp:
                temp.write(request.program.encode())
                filename = temp.name

        # Parse the program
        syntax = parse_program(filename)

        # Reduce the program
        program = reduce_program(syntax)

        # Clean up temporary file if created
        if not request.filename:
            os.unlink(filename)

        # Return the response
        return {
            "operations": len(program.operations),
            "goals": len(program.goals),
            "task_graph": str(program),
            "dot_graph": program.to_dot(),
            "syntax": str(syntax),
        }

    except VLException as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@api_app.post("/save-task-graph", response_model=Dict[str, str])
def api_save_task_graph(request: ProgramRequest) -> Dict[str, str]:
    """Parse, reduce, and save the task graph of a VoxLogicA program"""
    try:
        # Write the program to a temporary file
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".imgql", delete=False) as temp:
            temp.write(request.program.encode())
            filename = temp.name

        # Parse the program
        syntax = parse_program(filename)

        # Reduce the program
        program = reduce_program(syntax)

        # Generate output filename
        output_filename = request.filename or "task_graph.dot"

        # Save the task graph
        with open(output_filename, "w") as f:
            f.write(program.to_dot())

        # Clean up temporary file
        os.unlink(filename)

        return {"message": f"Task graph saved to {output_filename}"}

    except VLException as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


if __name__ == "__main__":
    app()
