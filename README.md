# 🚀 Guía de Despliegue de Aplicaciones

Este README contiene las instrucciones para desplegar aplicaciones en el servidor **10.20.12.32** utilizando Uvicorn y screen.

## 📋 Requisitos Previos

- Acceso SSH al servidor 10.20.12.32
- Entorno virtual de Python (venv) configurado
- Aplicación con archivo principal que contenga la instancia `app` de FastAPI/ASGI

## 🛠️ Pasos para el Despliegue

### 1. Conectarse al Servidor

```bash
ssh ubuntu@10.20.12.32
```

### 2. Navegar al Directorio de tu Aplicación

```bash
cd /joyastack
```

### 3. Crear una Nueva Sesión de Screen

```bash
screen
```

Presiona **Espacio** para continuar.

### 4. Activar el Entorno Virtual

```bash
source venv/bin/activate
```

Se debe ver `(venv)` al inicio de la línea de comandos.

### 5. Iniciar la Aplicación con Uvicorn

Utilizar el siguiente comando, reemplazando los valores según la utilización:

```bash
uvicorn <app_name>:app --host 0.0.0.0 --port <puerto>
```

**Ejemplo real:**
```bash
uvicorn slice_manager_api:app --host 0.0.0.0 --port 8001
```

Donde:
- `<app_name>` es el nombre del archivo Python (sin .py)
- `<puerto>` es el puerto asignado a la aplicación

### 6. Desconectar de Screen (sin cerrar la aplicación)

Presiona: **`CTRL + A`**, luego **`D`**

La aplicación seguirá ejecutándose en segundo plano.

##  Comandos Útiles de Screen

### Ver todas las sesiones activas
```bash
screen -ls
```

### Reconectar a una sesión existente
```bash
screen -r <session_id>
```

### Reconectar si solo hay una sesión
```bash
screen -r
```

### Matar una sesión de screen
```bash
screen -X -S <session_id> quit
```

##  Ejemplo Completo

```bash
# 1. Conectar al servidor
ssh ubuntu@10.20.12.32

# 2. Ir al directorio
cd ~/placement_manager_api

# 3. Crear screen
screen

# 4. Activar venv
source venv/bin/activate

# 5. Iniciar aplicación
uvicorn placement_manager_api:app --host 0.0.0.0 --port 8002

# 6. Desconectar: CTRL+A, luego D
```

## 📝 Puertos Asignados

Mantén un registro de los puertos utilizados para evitar conflictos:

| Aplicación            | Puerto | Responsable      |
|-----------------------|--------|------------------|
| auth_api              | 8001   | Jose Morillos    |
| slice_manager_api     | 8001   | Oscar Agreda     |
| placement_manager_api | 8002   | Alejandro Hancco |
| monitoring_api        | 8003   | Yosthim Enciso   |

## ⚠️ Notas Importantes

- Cada aplicación debe usar un **puerto único**
- El host `0.0.0.0` permite que la aplicación sea accesible desde cualquier interfaz de red
- No cerrar la terminal sin hacer **CTRL+A+D**, o la aplicación se detendrá
- Se puede tener múltiples screens activos simultáneamente

## 🔍 Verificar que tu Aplicación está Corriendo

```bash
# Ver procesos de uvicorn
ps aux | grep uvicorn

# Ver puertos en uso
netstat -tuln | grep LISTEN
```

## Consideraciones

### Si la aplicación no inicia
- Verifica que el entorno virtual esté activado
- Confirma que el nombre del archivo y el puerto sean correctos
- Revisa que el puerto no esté en uso: `lsof -i :<puerto>`

### Si no se puede acceder a mi screen
- Lista los screens: `screen -ls`
- Reconecta usando el ID específico: `screen -r <id>`

---