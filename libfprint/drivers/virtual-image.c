/*
 * Virtual driver for image device debugging
 *
 * Copyright (C) 2019 Benjamin Berg <bberg@redhat.com>
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2.1 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with this library; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
 */

/*
 * This is a virtual driver to debug the image based drivers. A small
 * python script is provided to connect to it via a socket, allowing
 * prints to be sent to this device programmatically.
 * Using this it is possible to test libfprint and fprintd.
 */

#define FP_COMPONENT "virtual_image"

#include "fpi-log.h"

#include "virtual-device-private.h"

#include "../fpi-image.h"
#include "../fpi-image-device.h"

struct _FpDeviceVirtualImage
{
  FpImageDevice            parent;

  FpDeviceVirtualListener *listener;
  GCancellable            *cancellable;

  gboolean                 automatic_finger;
  FpImage                 *recv_img;
  gint                     recv_img_hdr[2];
};

G_DECLARE_FINAL_TYPE (FpDeviceVirtualImage, fpi_device_virtual_image, FPI, DEVICE_VIRTUAL_IMAGE, FpImageDevice)
G_DEFINE_TYPE (FpDeviceVirtualImage, fpi_device_virtual_image, FP_TYPE_IMAGE_DEVICE)

static void recv_image (FpDeviceVirtualImage *self);

static void
recv_image_img_recv_cb (GObject      *source_object,
                        GAsyncResult *res,
                        gpointer      user_data)
{
  g_autoptr(GError) error = NULL;
  FpDeviceVirtualListener *listener = FP_DEVICE_VIRTUAL_LISTENER (source_object);
  FpDeviceVirtualImage *self;
  FpImageDevice *device;
  gsize bytes;

  bytes = fp_device_virtual_listener_read_finish (listener, res, &error);

  if (!bytes || g_error_matches (error, G_IO_ERROR, G_IO_ERROR_CANCELLED))
    return;

  self = FPI_DEVICE_VIRTUAL_IMAGE (user_data);
  device = FP_IMAGE_DEVICE (self);

  if (self->automatic_finger)
    fpi_image_device_report_finger_status (device, TRUE);
  fpi_image_device_image_captured (device, g_steal_pointer (&self->recv_img));
  if (self->automatic_finger)
    fpi_image_device_report_finger_status (device, FALSE);

  /* And, listen for more images from the same client. */
  recv_image (self);
}

static void
recv_image_hdr_recv_cb (GObject      *source_object,
                        GAsyncResult *res,
                        gpointer      user_data)
{
  g_autoptr(GError) error = NULL;
  FpDeviceVirtualImage *self;
  FpDeviceVirtualListener *listener = FP_DEVICE_VIRTUAL_LISTENER (source_object);
  gsize bytes;

  bytes = fp_device_virtual_listener_read_finish (listener, res, &error);

  if (error)
    {
      if (g_error_matches (error, G_IO_ERROR, G_IO_ERROR_CANCELLED))
        return;

      g_warning ("Error receiving header for image data: %s", error->message);
      return;
    }

  if (!bytes)
    return;

  self = FPI_DEVICE_VIRTUAL_IMAGE (user_data);
  if (self->recv_img_hdr[0] > 5000 || self->recv_img_hdr[1] > 5000)
    {
      g_warning ("Image header suggests an unrealistically large image, disconnecting client.");
      fp_device_virtual_listener_connection_close (listener);
    }

  if (self->recv_img_hdr[0] < 0 || self->recv_img_hdr[1] < 0)
    {
      switch (self->recv_img_hdr[0])
        {
        case -1:
          /* -1 is a retry error, just pass it through */
          fpi_image_device_retry_scan (FP_IMAGE_DEVICE (self), self->recv_img_hdr[1]);
          break;

        case -2:
          /* -2 is a fatal error, just pass it through*/
          fpi_image_device_session_error (FP_IMAGE_DEVICE (self),
                                          fpi_device_error_new (self->recv_img_hdr[1]));
          break;

        case -3:
          /* -3 sets/clears automatic finger detection for images */
          self->automatic_finger = !!self->recv_img_hdr[1];
          break;

        case -4:
          /* -4 submits a finger detection report */
          fpi_image_device_report_finger_status (FP_IMAGE_DEVICE (self),
                                                 !!self->recv_img_hdr[1]);
          break;

        default:
          /* disconnect client, it didn't play fair */
          fp_device_virtual_listener_connection_close (listener);
        }

      /* And, listen for more images from the same client. */
      recv_image (self);
      return;
    }

  self->recv_img = fp_image_new (self->recv_img_hdr[0], self->recv_img_hdr[1]);
  g_debug ("image data: %p", self->recv_img->data);
  fp_device_virtual_listener_read (listener,
                                   (guint8 *) self->recv_img->data,
                                   self->recv_img->width * self->recv_img->height,
                                   recv_image_img_recv_cb,
                                   self);
}

static void
recv_image (FpDeviceVirtualImage *self)
{
  fp_device_virtual_listener_read (self->listener,
                                   self->recv_img_hdr,
                                   sizeof (self->recv_img_hdr),
                                   recv_image_hdr_recv_cb,
                                   self);
}

static void
on_listener_connected (FpDeviceVirtualListener *listener,
                       gpointer                 user_data)
{
  FpDeviceVirtualImage *self = FPI_DEVICE_VIRTUAL_IMAGE (user_data);

  self->automatic_finger = TRUE;

  recv_image (self);
}

static void
dev_init (FpImageDevice *dev)
{
  g_autoptr(GError) error = NULL;
  g_autoptr(FpDeviceVirtualListener) listener = NULL;
  g_autoptr(GCancellable) cancellable = NULL;
  FpDeviceVirtualImage *self = FPI_DEVICE_VIRTUAL_IMAGE (dev);
  G_DEBUG_HERE ();

  listener = fp_device_virtual_listener_new ();
  cancellable = g_cancellable_new ();

  if (!fp_device_virtual_listener_start (listener,
                                         fpi_device_get_virtual_env (FP_DEVICE (self)),
                                         cancellable,
                                         on_listener_connected,
                                         self,
                                         &error))
    {
      fpi_image_device_open_complete (dev, g_steal_pointer (&error));
      return;
    }

  self->listener = g_steal_pointer (&listener);
  self->cancellable = g_steal_pointer (&cancellable);

  fpi_image_device_open_complete (dev, NULL);
}

static void
dev_deinit (FpImageDevice *dev)
{
  FpDeviceVirtualImage *self = FPI_DEVICE_VIRTUAL_IMAGE (dev);

  G_DEBUG_HERE ();

  g_cancellable_cancel (self->cancellable);
  g_clear_object (&self->cancellable);
  g_clear_object (&self->listener);

  fpi_image_device_close_complete (dev, NULL);
}

static void
fpi_device_virtual_image_init (FpDeviceVirtualImage *self)
{
}

static const FpIdEntry driver_ids[] = {
  { .virtual_envvar = "FP_VIRTUAL_IMAGE" },
  { .virtual_envvar = NULL }
};

static void
fpi_device_virtual_image_class_init (FpDeviceVirtualImageClass *klass)
{
  FpDeviceClass *dev_class = FP_DEVICE_CLASS (klass);
  FpImageDeviceClass *img_class = FP_IMAGE_DEVICE_CLASS (klass);

  dev_class->id = FP_COMPONENT;
  dev_class->full_name = "Virtual image device for debugging";
  dev_class->type = FP_DEVICE_TYPE_VIRTUAL;
  dev_class->id_table = driver_ids;

  img_class->img_open = dev_init;
  img_class->img_close = dev_deinit;
}
