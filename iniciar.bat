@echo off
chcp 65001 >nul
title Debitos em Aberto - Save Company
color 1F
cd /d "%~dp0"

echo.
echo  ============================================================
echo   Debitos em Aberto - Save Company
echo  ============================================================
echo.

:: Verifica se o main.py esta na mesma pasta que este arquivo
if not exist "%~dp0main.py" (
    echo  [ERRO] Arquivo main.py nao encontrado.
    echo.
    echo  Certifique-se de que todos os arquivos do projeto estao
    echo  na mesma pasta que este iniciar.bat:
    echo.
    echo  %~dp0
    echo.
    pause
    exit /b 1
)

:: Verifica se Python esta disponivel no PATH
where python >nul 2>&1
if errorlevel 1 (
    echo  [ERRO] Python nao encontrado no sistema.
    echo  Instale o Python em python.org e marque "Add to PATH"
    echo  durante a instalacao.
    echo.
    pause
    exit /b 1
)

python "%~dp0main.py"
if errorlevel 1 (
    echo.
    echo  [ERRO] A automacao encerrou com falha. Veja o log acima.
    echo  Pressione qualquer tecla para fechar...
    pause > nul
)
