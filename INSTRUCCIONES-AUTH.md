# 🔐 Instrucciones para configurar MiRadicado con Login de Google

## ✅ Pasos completados hasta ahora:
1. ✅ Proyecto Supabase creado (miradicado.co)
2. ✅ Credenciales OAuth de Google creadas
3. ✅ Google Provider configurado en Supabase
4. ✅ Usuario de prueba agregado (legumbresr@gmail.com)
5. ✅ Tablas de base de datos creadas (profiles, procesos, documentos, actuaciones)

---

## 🔑 Paso siguiente: Obtener la Anon Key de Supabase

Necesitas copiar la **anon key** de Supabase para que la aplicación funcione:

1. Ve a **Supabase Dashboard**: https://supabase.com/dashboard
2. Entra a tu proyecto **miradicado.com**
3. En el menú izquierdo, ve a: **Project Settings** (⚙️ icono de engranaje abajo)
4. Haz clic en **API** (en la sección Configuration)
5. Copia el valor de **anon public** key (empieza con `eyJ...`)

---

## 📝 Actualizar el archivo index-auth.html

Abre el archivo `index-auth.html` y busca esta línea (aproximadamente línea 430):

```javascript
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...placeholder';
```

Reemplázala con tu anon key real:

```javascript
const SUPABASE_ANON_KEY = 'TU_ANON_KEY_AQUI';
```

---

## 🚀 Probar la aplicación

1. Asegúrate de tener el servidor corriendo:
   ```
   py proxy_server.py
   ```

2. Abre en el navegador:
   ```
   http://localhost:8000/index-auth.html
   ```

3. Haz clic en **"Continuar con Google"**

4. Inicia sesión con tu cuenta de Google (legumbresr@gmail.com)

5. Como eres el primer usuario y admin, deberías entrar directamente

---

## 👥 Cómo funciona el sistema de usuarios

### Para el Admin (tú):
- Puedes ver todos los usuarios en "Administrar Usuarios"
- Aprobar o rechazar usuarios nuevos
- Hacer otros usuarios administradores

### Para nuevos usuarios:
1. Entran y hacen login con Google
2. Ven pantalla de "Pendiente de aprobación"
3. Tú los apruebas desde el panel de admin
4. Ellos pueden entrar y ver solo SUS procesos

---

## 🔧 Si tienes problemas

### "Error al iniciar sesión"
- Verifica que la anon key esté correcta
- Verifica que tu email esté en "Usuarios de prueba" en Google Cloud

### "Acceso denegado" en Google
- Ve a Google Cloud Console → Google Auth Platform → Público
- Agrega tu email como usuario de prueba

### No carga los procesos
- Verifica que el servidor proxy esté corriendo
- Abre la consola del navegador (F12) para ver errores

---

## 📂 Archivos

| Archivo | Descripción |
|---------|-------------|
| `index-auth.html` | Nueva versión con login de Google |
| `index.html` | Versión anterior (sin login) |
| `proxy_server.py` | Servidor proxy (sin cambios) |

---

## 🎯 Próximos pasos sugeridos

1. Probar el login con Google
2. Agregar un proceso de prueba
3. Invitar a otro abogado para probar el sistema multi-usuario
4. Publicar en Vercel (cuando esté listo)

---

¿Dudas? Pregúntame lo que necesites 😊
