/*
 * Fake usrvclock Protocol Server
 * Protocol specification: https://gitlab.com/lumifaza/usrvclock
 *
 * License:
 * Mostly written by AI so no authorship, no copyright, public domain.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/select.h>
#include <sys/stat.h>
#include <time.h>
#include <stdint.h>
#include <errno.h>
#include <signal.h>

#define MAX_CLIENTS 32
#define SOCKET_PATH_DEFAULT "/run/ptp-usrvclock"
#define BUF_SIZE 1024

typedef struct {
    uint8_t magic[2];   // 'V', 'C'
    uint16_t major;
    uint16_t minor;
    int16_t flags;
    int64_t clockid;
    int64_t last_sync;
    int64_t shift;
    double freq_scale;
} __attribute__((packed)) overlay_frame;

static int server_fd = -1;
static char *socket_path = NULL;
static struct sockaddr_storage clients[MAX_CLIENTS];
static socklen_t client_addr_len[MAX_CLIENTS];
static char client_slots[MAX_CLIENTS];

static void handle_signal(int sig) {
    if (server_fd > 0) {
        close(server_fd);
        if (socket_path) {
            unlink(socket_path);
        }
    }
    exit(0);
}

int main() {
    // Set up signal handler
    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    // Get socket path from environment or use default
    socket_path = getenv("USRVCLOCK_SOCKET");
    if (socket_path == NULL) {
        socket_path = SOCKET_PATH_DEFAULT;
    }

    // Create socket
    server_fd = socket(AF_UNIX, SOCK_DGRAM, 0);
    if (server_fd < 0) {
        perror("socket creation failed");
        exit(EXIT_FAILURE);
    }

    // Unlink existing socket if present
    unlink(socket_path);

    // Bind
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, socket_path, sizeof(addr.sun_path) - 1);

    if (bind(server_fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        perror("bind failed");
        close(server_fd);
        exit(EXIT_FAILURE);
    }
    chmod(socket_path, 0777);

    memset(client_slots, 0, MAX_CLIENTS);

    // Main loop
    while (1) {
        fd_set read_fds;
        FD_ZERO(&read_fds);
        FD_SET(server_fd, &read_fds);

        struct timeval timeout;
        timeout.tv_sec = 1;
        timeout.tv_usec = 0;

        int ready = select(server_fd + 1, &read_fds, NULL, NULL, &timeout);
        if (ready < 0) {
            if (errno == EINTR) {
                continue;
            }
            perror("select failed");
            break;
        }

        // Timeout: send overlay to all clients
        struct timespec ts;
        if (clock_gettime(CLOCK_MONOTONIC_RAW, &ts) < 0) {
            perror("clock_gettime failed");
            continue;
        }
        int64_t last_sync = (int64_t)ts.tv_sec * 1000000000 + ts.tv_nsec;

        overlay_frame frame;
        frame.magic[0] = 'V';
        frame.magic[1] = 'C';
        frame.major = 1;
        frame.minor = 0;
        frame.flags = 0x0001; // valid
        frame.clockid = CLOCK_MONOTONIC_RAW;
        frame.last_sync = last_sync;
        frame.shift = 0;
        frame.freq_scale = 0.0;

        if (ready == 0) {
            for (int i = 0; i < MAX_CLIENTS; i++) {
                if (!client_slots[i]) continue;
                if (sendto(server_fd, &frame, sizeof(frame), 0,
                           (struct sockaddr*)&clients[i], client_addr_len[i]) < 0) {
                    perror("sendto failed");
                    client_slots[i] = 0;
                }
            }
        } else {
            // There is data to read
            struct sockaddr_storage client_addr;
            socklen_t addr_len = sizeof(client_addr);
            char buf[BUF_SIZE];
            ssize_t n = recvfrom(server_fd, buf, sizeof(buf), 0,
                                 (struct sockaddr*)&client_addr, &addr_len);
            if (n < 0) {
                perror("recvfrom failed");
                continue;
            }

            if (sendto(server_fd, &frame, sizeof(frame), 0,
                        (struct sockaddr*)&client_addr, addr_len) < 0) {
                perror("initial sendto failed");
            }

            // Check if client already in list
            int found = 0;
            for (int i = 0; i < MAX_CLIENTS; i++) {
                if (!client_slots[i]) continue;
                // Compare the addresses. We compare the length and the bytes.
                if (client_addr_len[i] == addr_len &&
                    memcmp(&clients[i], &client_addr, addr_len) == 0) {
                    found = 1;
                    break;
                }
            }

            if (!found) {
                int free_slot = -1;
                for (int i = 0; i < MAX_CLIENTS; i++) {
                    if (!client_slots[i]) {
                        free_slot = i;
                        break;
                    }
                }
                if (free_slot >= 0) {
                    // Add new client
                    memcpy(&clients[free_slot], &client_addr, addr_len);
                    client_addr_len[free_slot] = addr_len;
                    client_slots[free_slot] = 1;
                } else {
                    fprintf(stderr, "too many clients\n");
                }
            }
        }
    }

    // Clean up
    close(server_fd);
    unlink(socket_path);
    return 0;
}
