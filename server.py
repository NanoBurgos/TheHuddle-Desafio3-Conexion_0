import socket
import threading
import traceback # Permite imprimir errores completos con información detallada
import queue #para poder crear una cola de envio para cada cliente
import time

HOST = '127.0.0.1'
PORT = 5000

server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) #para reutilizar inmediatamente esta dirección IP y puerto
server_sock.bind((HOST, PORT))
server_sock.listen()
server_sock.settimeout(1) #espera 1 segundo a las acciones bloqueantes

# clients: socket {"name": str, "queue": Queue, }
clients = {}
clients_lock = threading.Lock() #para evitar condiciones de carrera (solo uno a la vez)
shutting_down = threading.Event() #usamos para indicar un cierre
threads = []


def safe_close(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass
    
    try:
        sock.close() #liberamos el socket
    except Exception:
        pass


def sender_thread_func(sock, send_queue, name):
    """
    Saca mensajes de send_queue y los envía con sendall.
    Si hay error de envío, notifica al servidor (removerá el cliente).
    """
    try:
        while True:
            try:
                item = send_queue.get()
            except Exception:
                break
            if item is None:
                # señal de cierre
                break
            try:
                sock.sendall(item)
            except Exception:
                # si falla el send, ponemos la cola en un estado que indique cierre
                print(f"XXXX Error enviando a {name}, forzando desconexión.")
                # notificar al controlador de cliente cerrándolo desde aquí podría ser arriesgado,
                # mejor señalamos el fin para que el recv/manager lo limpie.
                break
    finally:
        # aseguramos que la cola sea limpiada / cerrada por el que maneja el cliente
        return


def broadcast(msg_bytes, exclude_sock=None):
    """
    Encola msg_bytes en la cola de cada cliente (excepto exclude_sock).
    No hace I/O de red aquí.
    """
    with clients_lock:
        targets = [s for s in clients.keys() if s is not exclude_sock]
        # hacemos copia de referencias a las colas
        queues = [clients[s]["queue"] for s in targets]

    for q in queues:
        try:
            # poner una copia de bytes para seguridad
            q.put(msg_bytes)
        except Exception:
            # si no se puede encolar, no dejamos que eso rompa el broadcast
            traceback.print_exc()


def handle_client(sock, addr):
    """
    Manager por cliente:
      - recibe el nombre,
      - crea una cola y un sender thread,
      - recibe mensajes y encola broadcasts,
      - limpia todo al desconectar.
    """
    name = None
    send_queue = queue.Queue()
    send_thread = None
    try:
        sock.settimeout(10)  # timeout para el primer recv (nombre)
        sock.sendall(" Bienvenido al chat. Escribí tu nombre: ".encode('utf-8'))
        data = sock.recv(1024)
        if not data:
            safe_close(sock)
            return
        name = data.decode('utf-8', errors='replace').strip() or "Anon"

        # poner cliente en dict con su cola
        with clients_lock:
            clients[sock] = {"name": name, "queue": send_queue, "addr": addr}

        # arrancar sender thread (daemon, porque su trabajo es auxiliar)
        send_thread = threading.Thread(target=sender_thread_func, args=(sock, send_queue, name), daemon=True)
        send_thread.start()

        print(f"🟢 {name} conectado desde {addr}")
        broadcast(f" {name} se unió al chat.\n".encode('utf-8'), exclude_sock=sock)

        # cambiar timeout a corto para permitir comprobaciones periódicas
        sock.settimeout(1.0)
        # loop receptor
        while not shutting_down.is_set():
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            except (ConnectionResetError, ConnectionAbortedError, OSError):
                # cierre forzado o error de red
                break
            except Exception:
                traceback.print_exc()
                break

            if not data:
                # cliente cerró conexión ordenadamente
                break

            text = data.decode('utf-8', errors='replace')
            if not text.endswith("\n"):
                text = text + "\n"
            # en vez de enviar por socket, encolamos para los otros (no bloquea)
            broadcast(f"{name}: {text}".encode('utf-8'), exclude_sock=sock)

    except Exception as e:
        print("XXX Excepción en handle_client:", e)
        traceback.print_exc()

    finally:
        # limpieza: quitar cliente del mapa, avisar a otros, señalizar cierre al sender
        try:
            with clients_lock:
                if sock in clients:
                    left = clients.pop(sock)["name"]
                else:
                    left = name or "Desconocido"
            print(f"🔴 {left} desconectado")
            # avisar a los demas
            broadcast(f" {left} abandonó el chat.\n".encode('utf-8'), exclude_sock=sock)
        finally:
            # señalizamos al sender para que termine (ponemos None en la cola)
            try:
                send_queue.put(None)
            except Exception:
                pass
            # cerramos socket
            safe_close(sock)
            # esperamos al sender un poquito
            if send_thread:
                send_thread.join(timeout=0.5)


def main():
    print(f"Servidor escuchando en {HOST}:{PORT}")
    try:
        while True:
            try:
                client_sock, addr = server_sock.accept()
            except socket.timeout:
                if shutting_down.is_set():
                    break
                continue
            except OSError:
                break

            t = threading.Thread(target=handle_client, args=(client_sock, addr), daemon=False)
            t.start()
            threads.append(t)

    except KeyboardInterrupt:
        print("\nCtrl+C detectado — iniciando cierre ordenado...")
        shutting_down.set()
    finally:
        # señalizar cierre y cerrar sockets para despertar recv()
        with clients_lock:
            socks = list(clients.keys())

        for s in socks:
            try:
                # meter None en su cola para parar su sender (si llegamos a la cola)
                with clients_lock:
                    if s in clients:
                        clients[s]["queue"].put(None)
                safe_close(s)
            except Exception:
                pass

        try:
            server_sock.close()
        except Exception:
            pass

        # esperar threads
        for t in threads:
            t.join(timeout=0.5)

        with clients_lock:
            clients.clear()

        print("✅ Servidor cerrado limpiamente.")


if __name__ == "__main__":
    main()
