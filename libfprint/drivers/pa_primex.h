
#define PA_HEADER_LEN 5
#define PA_LEN_LEN 2
#define PA_INNER_HEADER_LEN 7
#define PA_SW_LEN 2
#define PA_MAX_GET_LEN 256
#define PA_APDU_CLA 0xfe
#define PA_CMD_ENROLL 0x71
#define PA_CMD_DELETE 0x73
#define PA_CMD_ABORT 0x74
#define PA_CMD_FPSTATE 0x75
#define PA_CMD_LIST 0x76
#define PA_CMD_VERIFY 0x80
#define PA_CMD_VID 0x81

#define PA_MAX_FINGER_COUNT 10

#define PA_OK 0
#define PA_FPM_CONDITION 1
#define PA_FPM_REFDATA 2
#define PA_BUSY 3
#define PA_P1P2 4
#define PA_NOSPACE 5
#define PA_ERROR -1

#define PA_FPM_ENROLL_OK 0xe1
#define PA_FPM_ENROLL_GOOD 0xe4
#define PA_FPM_ENROLL_CANCEL 0xe3
#define PA_FPM_ENROLL_REDUNDANT 0xe5
#define PA_FPM_ENROLL_NOFINGER 0xe7
#define PA_FPM_ENROLL_NOTFULLFINGER 0xe8
#define PA_FPM_ENROLL_WAITING 0xe0
#define PA_FPM_VERIFY_WAITING 0xf0
#define PA_FPM_VERIFY_OK 0xf1
#define PA_FPM_VERIFY_FAIL 0xf2
#define PA_FPM_VERIFY_CANCEL 0xf3
#define PA_FPM_IDLE 0

#define TIMEOUT 5000
#define PA_IN (2 | FPI_USB_ENDPOINT_IN)
#define PA_OUT (1 | FPI_USB_ENDPOINT_OUT)

const unsigned char pa_header[] = {0x50, 0x58, 0x41, 0x54, 0xc0};
const char *str_enroll = "u2f enroll fp";
const char *str_delete = "u2f delete fp";
const char *str_abort = "u2f abort fp";
const char *str_verify = "wbf verify fp";

#define STORAGE_FILE "pa-storage.variant"

enum pa_primex_driver_data
{
    PRIMEX,
};

enum initpa_states
{
    ABORT_PUT = 0,
    ABORT_GET,
    INIT_DONE,
};

enum enroll_start_pa_states
{
    ENROLL_LIST_BEFORE_SEND = 0,
    ENROLL_LIST_BEFORE_GET,
    ENROLL_CMD_SEND,
    ENROLL_CMD_GET,
    ENROLL_UPDATE
};

enum enroll_finish_pa_states
{
    ENROLL_LIST_AFTER_SEND = 0,
    ENROLL_LIST_AFTER_GET,
    ENROLL_DONE
};


enum list_pa_states
{
    LIST_AFTER_SEND = 0,
    LIST_AFTER_GET,
    LIST_AFTER_DONE
};

enum verify_start_pa_states
{
    VERIFY_CMD_SEND = 0,
    VERIFY_CMD_GET,
    VERIFY_UPDATE
};

enum verify_finish_pa_states
{
    VERIFY_GET_ID_SEND = 0,
    VERIFY_GET_ID_GET,
    VERIFY_FINAL
};

enum delete_cmd_states
{
    DELETE_SEND = 0,
    DELETE_GET,
    DELETE_DONE
};

enum list_cmd_states
{
    LIST_SEND = 0,
    LIST_GET,
    LIST_DONE
};

static const FpIdEntry id_table[] = {
    {.vid = 0x2F0A, .pid = 0x0201, .driver_data = PRIMEX},
    {.vid = 0, .pid = 0, .driver_data = 0},
};

typedef struct
{
    FpPrint *print;
    GError *error;
} EnrollStopData;

typedef struct pa_finger_list
{
  int                total_number;                                   /**< Total query response messages */
  unsigned char      finger_map[PA_MAX_FINGER_COUNT];
  int                modified_by;
} pa_finger_list_t;

typedef void (*handle_get_fn)(FpDevice *dev,
                              unsigned char *data,
                              size_t data_len,
                              void *user_data,
                              GError *error);

struct prime_data
{
    gssize buflen;
    guint8 *buffer;
    handle_get_fn callback;
    void *user_data;
};

const char* pa_description = "/dev/";

/*Storage group*/
static char *get_pa_data_descriptor (FpPrint *print, FpDevice *dev, FpFinger finger);
static GVariantDict *_load_data (void);
static int _save_data (GVariant *data);
FpPrint *pa_data_load (FpDevice *dev, FpFinger finger);
int pa_data_save (FpPrint *print, FpFinger finger);
int pa_data_del(FpDevice *dev, FpFinger finger);
int get_dev_index(FpDevice* dev, FpPrint *print);

/*USB layer group*/
static void
alloc_send_cmd_transfer(FpDevice *dev,
                        FpiSsm *ssm,
                        unsigned char ins,
                        unsigned char p1,
                        unsigned char p2,
                        const unsigned char *data,
                        guint16 len);
static void alloc_get_cmd_transfer(FpDevice *device, handle_get_fn callback, void *user_data);
static void read_cb(FpiUsbTransfer *transfer, FpDevice *device, gpointer user_data, GError *error);
static void handle_response(FpDevice *device, FpiUsbTransfer *transfer, struct prime_data *udata);
static int get_sw(unsigned char *data, size_t data_len);
static int get_data(unsigned char *data, size_t data_len, unsigned char *buf);

/* Init group*/
static void dev_init(FpDevice *dev);
static void initpa_run_state(FpiSsm *ssm, FpDevice *dev);
static void handle_get_abort(FpDevice *dev,
                             unsigned char *data,
                             size_t data_len,
                             void *user_data,
                             GError *error);
static void initpa_done(FpiSsm *ssm, FpDevice *dev, GError *error);

/*Deinit group*/
static void dev_exit(FpDevice *dev);

/*Enroll group*/
static void enroll(FpDevice *dev);
static void enroll_start_pa_run_state(FpiSsm *ssm, FpDevice *dev);
static void handle_get_enroll(FpDevice *dev,
                              unsigned char *data,
                              size_t data_len,
                              void *user_data,
                              GError *error);
static void enroll_iterate(FpDevice *dev);
static void
enroll_iterate_cmd_cb(FpiUsbTransfer *transfer,
                      FpDevice *device,
                      gpointer user_data,
                      GError *error);
static void
handle_enroll_iterate_cb(FpDevice *dev,
                         unsigned char *data,
                         size_t data_len,
                         void *user_data,
                         GError *error);
static void enroll_started(FpiSsm *ssm, FpDevice *dev, GError *error);
static void do_enroll_done(FpDevice *dev);
static void enroll_save(FpiSsm *ssm, FpDevice *dev, GError *error);
static void enroll_finish_pa_run_state(FpiSsm *ssm, FpDevice *dev);
static void gen_finger(FpDevice *device, int dev_index, FpPrint *print);

/*Verify group*/
static void verify(FpDevice *dev);
static void verify_start_pa_run_state(FpiSsm *ssm, FpDevice *dev);
static void verify_iterate(FpDevice *dev);
static void verify_started(FpiSsm *ssm, FpDevice *dev, GError *error);
static void
handle_get_vid(FpDevice *dev,
                            unsigned char *data,
                            size_t data_len,
                            void *user_data,
                            GError *error);
static void verify_iterate_cmd_cb(FpiUsbTransfer *transfer, FpDevice *device,gpointer user_data, GError *error);
static void
handle_verify_iterate_cb(FpDevice *dev,
                         unsigned char *data,
                         size_t data_len,
                         void *user_data,
                         GError *error);
static void do_verify_done(FpDevice *dev);  
static void verify_finish_pa_run_state(FpiSsm *ssm, FpDevice *dev);
static void verify_report(FpiSsm *ssm, FpDevice *dev, GError *error);                     
/*List group*/
static void list(FpDevice *device);
static void list_done(FpiSsm *ssm, FpDevice *device, GError *error);
static void handle_get_list(FpDevice *dev,
                              unsigned char *data,
                              size_t data_len,
                              void *user_data,
                              GError *error);
static void list_pa_run_state(FpiSsm *ssm, FpDevice *dev);

/*Delete group*/
static void delete (FpDevice *device);
static void delete_cmd_state(FpiSsm *ssm, FpDevice *dev);
static void handle_get_delete(FpDevice *dev,
                              unsigned char *data,
                              size_t data_len,
                              void *user_data,
                              GError *error);
static void delete_done(FpiSsm *ssm, FpDevice *dev, GError *error);