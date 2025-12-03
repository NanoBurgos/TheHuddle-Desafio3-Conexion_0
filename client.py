import socket
import threading
import time
import sys

SERVER_HOST = '127.0.0.1'
SERVER_PORT = 5000
RECONNECT_DELAY = 3  # segundos entre reintentos
RECV_TIMEOUT = 1.0   # timeout para recv() para poder comprobar flags periódicamente


def connect_to_server():
    """Intenta conectar en bucle y devuelve un socket configurado con timeout."""
    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)  # timeout para connect
            sock.connect((SERVER_HOST, SERVER_PORT))
            sock.settimeout(RECV_TIMEOUT)  # timeout para recv en el hilo receptor
            return sock
        except (ConnectionRefusedError, OSError):
            print(f"XXXX No se pudo conectar al servidor, reintentando en {RECONNECT_DELAY} s...")
            time.sleep(RECONNECT_DELAY)


def receive_loop(sock, stop_event):
    """
    Hilo que recibe mensajes del servidor.
    Usa timeout en recv para poder salir ordenadamente si stop_event está puesto.
    NO llama a sys.exit() ni cierra todo el proceso.
    """
    try:
        while not stop_event.is_set():
            try:
                data = sock.recv(4096)
            except socket.timeout:
                # chequeamos stop_event periódicamente
                continue
            except (ConnectionResetError, ConnectionAbortedError, OSError):
                # el servidor cerró la conexión o hubo error de red
                print("\nXXXX Conexión con el servidor perdida.")
                stop_event.set()
                break

            if not data:
                # recv devolvió b'' -> conexión cerrada por el servidor
                print("\nXXXX El servidor cerró la conexión.")
                stop_event.set()
                break

            # Imprimimos el mensaje respetando el prompt
            try:
                msg = data.decode('utf-8', errors='replace')
            except Exception:
                msg = repr(data)
            # \r para sobreescribir prompt si hace falta, y luego reimprimir prompt
            print(f"\r{msg}\n> ", end='', flush=True)
    finally:
        # no cerramos sock aquí: lo cerrará el thread principal o la rutina de reconexión
        return


def main():
    sock = connect_to_server()

    # Recibimos prompt de bienvenida (bloqueante breve)
    try:
        sock.settimeout(5)  # timeout menor mientras esperamos el prompt inicial
        welcome = sock.recv(1024).decode('utf-8')
    except Exception as e:
        print(f"XXXX Error al recibir prompt del servidor: {e}")
        try:
            sock.close()
        except:
            pass
        return
    finally:
        sock.settimeout(RECV_TIMEOUT)

    if not welcome:
        print("XXXX No recibí prompt del servidor. Saliendo.")
        return

    name = input(welcome + "\n> ").strip() or "Anon"
    try:
        sock.sendall(name.encode('utf-8'))
    except Exception as e:
        print(f"XXXX Error al enviar nombre: {e}")
        sock.close()
        return

    stop_event = threading.Event()
    receiver = threading.Thread(target=receive_loop, args=(sock, stop_event), daemon=False)
    receiver.start()

    try:
        while True:
            try:
                msg = input("> ")
            except EOFError:
                # por si cierran la terminal
                msg = 'exit'

            if msg.lower() in ('exit', 'quit', 'salir'):
                print(" Cerrando conexión...")
                stop_event.set()
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    sock.close()
                except:
                    pass
                receiver.join(timeout=1.0)
                break

            # intento de envío
            try:
                sock.sendall(msg.encode('utf-8'))
            except (BrokenPipeError, ConnectionResetError, OSError):
                # conexión perdida: intentamos reconectar y reestablecer hilo receptor
                print("XXXX No se puede enviar. Conexión perdida. Intentando reconectar...")
                stop_event.set()
                try:
                    sock.close()
                except:
                    pass
                receiver.join(timeout=1.0)

                # reconectar
                sock = connect_to_server()
                # reenviamos nombre al reconectar
                try:
                    sock.sendall(name.encode('utf-8'))
                except Exception as e:
                    print(f"XXXX Error al reenviar nombre tras reconectar: {e}")
                    continue

                # nuevo stop_event y nuevo hilo receptor
                stop_event = threading.Event()
                receiver = threading.Thread(target=receive_loop, args=(sock, stop_event), daemon=False)
                receiver.start()
    except KeyboardInterrupt:
        print("\n Interrumpido por teclado.")
        stop_event.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            sock.close()
        except:
            pass
        receiver.join(timeout=1.0)


if __name__ == "__main__":
    main()
