/*
 * Copyright (C) 2020 Benjamin Berg <bberg@redhat.com>
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

#pragma once

#include <glib-object.h>

#if !GLIB_CHECK_VERSION (2, 57, 0)
G_DEFINE_AUTOPTR_CLEANUP_FUNC (GTypeClass, g_type_class_unref);
G_DEFINE_AUTOPTR_CLEANUP_FUNC (GEnumClass, g_type_class_unref);
G_DEFINE_AUTOPTR_CLEANUP_FUNC (GParamSpec, g_param_spec_unref);
#else
/* Re-define G_SOURCE_FUNC as we are technically not allowed to use it with
 * the version we depend on currently. */
#undef G_SOURCE_FUNC
#endif

#define G_SOURCE_FUNC(f) ((GSourceFunc) (void (*)(void))(f))

#if !GLIB_CHECK_VERSION (2, 63, 3)
typedef struct _FpDeviceClass FpDeviceClass;
G_DEFINE_AUTOPTR_CLEANUP_FUNC (FpDeviceClass, g_type_class_unref);
G_DEFINE_AUTOPTR_CLEANUP_FUNC (GDate, g_date_free);
#endif

#if  __GNUC__ > 10 || (__GNUC__ == 10 && __GNUC_MINOR__ >= 1)
#define FP_GNUC_ACCESS(m, p, s) __attribute__((access (m, p, s)))
#else
#define FP_GNUC_ACCESS(m, p, s)
#endif

#if !GLIB_CHECK_VERSION (2, 61, 2)
inline GPtrArray *
g_ptr_array_copy (GPtrArray *array,
                  GCopyFunc  func,
                  gpointer   user_data)
{
  GPtrArray *new_array;

  g_return_val_if_fail (array != NULL, NULL);

  g_assert ((!array->len || G_IS_OBJECT (array->pdata[0])) &&
            (func == (GCopyFunc) g_object_ref || func == NULL) &&
            "Only arrays of objects are supported here!");

  new_array = g_ptr_array_new_full (array->len, g_object_unref);

  if (array->len > 0)
    memcpy (new_array->pdata, array->pdata,
            array->len * sizeof (*array->pdata));

  new_array->len = array->len;

  g_ptr_array_foreach (array, (GFunc) func, user_data);

  return new_array;
}
#endif
